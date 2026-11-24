#!/usr/bin/env python3
"""M6-lite -- the evaluator that turns one run plus the human reference into the frozen
metric tables. PIPELINE-COMPLETION-PLAN.md Step 3; every rule here was fixed before any
evaluation prediction existed.

    .venv/bin/python3 tools/pipeline/evaluate.py --run-record tools/runs/<run_id>.json \\
        --manifest tools/annotation/labels/A2_sample_manifest.json \\
        --gold-pass1 tools/annotation/labels/A2_pass1_v1.0.csv \\
        --gold-pass2 tools/annotation/labels/A2_pass2_v1.0.csv --threshold <s>
    ... --manifest tools/annotation/labels/A1_manifest.json --restrict-split A1 --dev   # dry run

Universe        outer join of gold and predicted (paragraph x technology) rows for execution 1 over
                EVERY manifest paragraph; gold off-topic paragraphs have an empty gold set, so
                inventions on them are false positives; a failed execution 1 has no predicted rows
Headline        design-weighted micro-F1 over (paragraph x technology x element) tuples, pooled and
                per language; the unweighted figure beside it
Categorical     score A: classification conditional on the parent being detected (matched units);
                score B: end-to-end labelled tuples (TP/FP/FN over (paragraph, technology, frame, value))
Class manifest  frozen once from the reference, per table and per language, published as JSON
Resampling      B article resamples, per language for per-language figures, global for pooled;
                an article drawn m times contributes with weights x m; the resampled macro is the
                AVAILABLE-CLASS macro-F1 and is named so
Reportability   per-class cells print a value when gold support >= s rows, else "n<s"; undefined
                (zero support and zero predictions) stays undefined; suppression never changes an
                aggregate
Stability       RQ3 on canonical objects over the union of technologies of successful executions;
                empty union -> no relation-level score; failures counted separately
Displacement    RQ4: design-weighted, reference-relative prevalence displacement for the eligible
                frame, paragraph-level presence, all manifest paragraphs in the denominator
Eligibility     refuses to write reporting tables unless the run record says reportable and the
                file digests match; --dev writes DEVELOPMENT-stamped tables to tables/dev/
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNS = ROOT / "tools" / "runs"
sys.path.insert(0, str(HERE))
import instrument as ins                                        # noqa: E402
import sample_contract as sc                                    # noqa: E402
import run_extract as rx                                        # noqa: E402  (journal format and reconciliation)

ELEMENTS = ["F1a", "F1b", "F2a", "F2b", "F2c", "F3a", "F3b", "F4a", "F4b", "F5a", "F5b",
            "F6a", "F6b", "F7a", "F7b", "F8a", "F9"]


def inventories() -> dict:
    """The complete class inventory per table, from the frozen codebook -- so unsupported
    classes appear in the per-class tables with support 0 rather than vanishing."""
    doc, _, _ = ins.load_codebook()
    techs = [t["id"] for t in doc["technologies"]]
    evals = [v["id"] for v in doc["evaluation"]["values"]]
    targets = [v["id"] if isinstance(v, dict) else v for v in doc["comparison_target"]["values"]]
    return {"technology": techs, "element": list(ELEMENTS), "evaluation_A": evals, "evaluation_B": evals,
            "comparison_A": targets, "comparison_B": targets}
FRAMES = ["costs", "jobs", "climate", "security", "reliability", "feasibility", "siting",
          "regulation", "other"]
SUCCESS = {"technologies_present", "off_topic"}
ABSENT = "__absent__"
UNDEFINED = "undefined"
DEFAULT_B = 2000
SEED = 20260905


# --------------------------------------------------------------------------- #
# small arithmetic
# --------------------------------------------------------------------------- #
def f1(tp: float, fp: float, fn: float):
    d = 2 * tp + fp + fn
    return None if d == 0 else 2 * tp / d


def fmt(x, nd=3):
    return UNDEFINED if x is None else f"{x:.{nd}f}"


def pct(xs, q):
    if not xs:
        return None
    ys = sorted(xs); k = (len(ys) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(ys) - 1)
    return ys[lo] + (ys[hi] - ys[lo]) * (k - lo)


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# canonical objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Rel:
    elements: frozenset
    evals: tuple            # sorted (frame, value)
    target: str
    subtype: str

    @property
    def frames(self) -> frozenset:
        return frozenset(f for f, _ in self.evals)


def rel_from_row(r: dict, frame_of: dict) -> Rel:
    els = frozenset(e for e in ELEMENTS if str(r.get(e, "")).strip() == "1")
    evals = tuple(sorted((c[len("eval_"):], str(r[c]).strip()) for c in r
                         if c.startswith("eval_") and str(r.get(c, "")).strip()))
    sub = str(r.get("wind_siting") or r.get("hydrogen_pathway") or "").strip()
    return Rel(els, evals, str(r.get("comparison_target", "")).strip(), sub)


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #
class EvalError(RuntimeError):
    pass


# The frozen frame and the registry a REPORTABLE evaluation must hand-shake with. Module
# globals so a fixture test can point them at temporary files; the pipeline never imports
# split_registry (a different interpreter), it reads the two JSON records directly.
FRAME_MANIFEST = ROOT / "tools/annotation/labels/A2_frame_manifest.json"
REGISTRY_FILE = ROOT / "tools/annotation/split-assignment.json"


def instrument_identity(rec: dict) -> dict:
    """The instrument a run bound, read from its execution contract (the document the journal's fingerprint
    binds), cross-checked against the record's display block. Records written before 2026-10-14 carry no
    instrument fields and are the registered v1.0 under retry policy v2."""
    contract = rec.get("execution_contract") or {}
    iv = str(contract.get("instrument_version", "1.0"))
    ident = {"name": "v1.0" if iv == "1.0" else f"v{iv}", "version": iv,
             "variant": None if iv in ("1.0", "1.0r3", "1.0F", "1.0C") else {"1.1S": "S", "1.1E": "E", "1.2S": "T"}.get(iv, iv[-1]),
             "retry_policy_version": str(contract.get("retry_policy_version", "2")),
             "instrument_v11_sha256": contract.get("instrument_v11_sha256"),
             "instrument_v10r3_sha256": contract.get("instrument_v10r3_sha256"),
             "instrument_v10f_sha256": contract.get("instrument_v10f_sha256"),
             "instrument_v10c_sha256": contract.get("instrument_v10c_sha256"),
             "prompt_path": contract.get("prompt_path", "tools/pipeline/instrument/system-prompt_v1.0.txt")}
    shown = rec.get("instrument")
    if isinstance(shown, dict):
        for k_ in ("name", "retry_policy_version"):
            if str(shown.get(k_)) != str(ident[k_]):
                raise EvalError(f"the run record's instrument block says {k_}={shown.get(k_)!r} but its execution "
                                f"contract says {ident[k_]!r}; the record was edited")
    if ident["name"] == "v1.0" and ident["instrument_v11_sha256"]:
        raise EvalError("a v1.0 run must not carry an instrument_v11.py digest in its execution contract")
    if ident["name"] != "v1.0" and not ident["instrument_v11_sha256"]:
        raise EvalError("a v1.1 run must carry the instrument_v11.py digest in its execution contract")
    # §4b candidate (2026-10-26): v1.0r3 carries its own module digest and retry policy v3; nothing else carries them
    if ident["name"] == sc.A3_CANDIDATE and not ident["instrument_v10r3_sha256"]:
        raise EvalError("a v1.0r3 run must carry the instrument_v10r3.py digest in its execution contract")
    if ident["name"] != sc.A3_CANDIDATE and ident["instrument_v10r3_sha256"]:
        raise EvalError("only a v1.0r3 run carries an instrument_v10r3.py digest in its execution contract")
    if ident["name"] == sc.A3_CANDIDATE and ident["retry_policy_version"] != "3":
        raise EvalError("v1.0r3 is the registered request under retry policy v3 (specification §4b)")
    # §4j (2026-10-30): v1.0F carries its own module digest and retry policy v3; nothing else carries the digest
    if (ident["name"] == sc.FORM_VARIANT) != bool(ident["instrument_v10f_sha256"]):
        raise EvalError("a v1.0F run, and only a v1.0F run, carries the instrument_v10f.py digest in its execution contract")
    if ident["name"] == sc.FORM_VARIANT and ident["retry_policy_version"] != "3":
        raise EvalError("v1.0F runs under retry policy v3 (specification §4j)")
    # §4l (5) (2026-11-06): v1.0C carries its own module digest and the registered retry policy v2; nothing else carries it
    if (ident["name"] == sc.CONTEXT_VARIANT) != bool(ident["instrument_v10c_sha256"]):
        raise EvalError("a v1.0C run, and only a v1.0C run, carries the instrument_v10c.py digest in its execution contract")
    if ident["name"] == sc.CONTEXT_VARIANT and ident["retry_policy_version"] != "2":
        raise EvalError("v1.0C runs under the registered retry policy v2 (specification §4l (5))")
    return ident


def analysis_status_of(rec: dict, manifest_kind: str | None = None) -> str:
    """'registered primary' | 'development' | 'exploratory reused A2' | 'fresh-sample evaluation' (Codex W3 round 3,
    #8; A3 2026-10-14). A reportable run of a revised instrument is exploratory by construction and must say so in
    its record; a reportable run over the A3 census is the fresh-sample evaluation of §4b and must say so."""
    ident = instrument_identity(rec)
    status = rec.get("analysis_status")
    if rec.get("dev"):
        if status not in (None, "development"):
            raise EvalError(f"a development run cannot carry analysis_status {status!r}")
        if rec.get("translated_input") or (rec.get("execution_contract") or {}).get("translated_input"):
            raise EvalError("a translated-input run (specification §4e(a)) is never a development run")
        # Codex §4j round 1 (#2): a §4j development run (v1.0F, or v1.0r3 with a §4j block) is checked before the shortcut
        contract_ = rec.get("execution_contract") or {}
        form_, form_c_ = rec.get("form_variant"), contract_.get("form_variant")
        if ident["name"] in (sc.FORM_VARIANT, sc.A3_CANDIDATE) or form_ or form_c_:    # v1.0r3 runs in development only under §4j
            if not (isinstance(form_, dict) and (form_.get("decision") or "").strip() and form_.get("authority") == sc.FORM_AUTHORITY
                    and form_.get("configuration") == ident["name"] and form_.get("sample") == "A1"):
                raise EvalError("a §4j development run must carry the author's §4j decision for its configuration on A1")
            if form_c_ != form_:
                raise EvalError("the run record's §4j block is not the one its execution contract binds")
            if int(rec.get("repeats") or 0) != sc.FORM_REPEATS["A1"] or ident["retry_policy_version"] != "3":
                raise EvalError("the §4j development run has k = 1 under retry policy v3")
            verify_form_development(rec)
        return "development"
    if manifest_kind == sc.FRAME_COMPLETION_KIND or status == sc.FRAME_COMPLETION_STATUS:
        raise EvalError("a frame-completion run has no reference and is never scored; it feeds the DSL analysis "
                        "(tools/pipeline/dsl.py) only (specification §4d)")
    # §4f (2026-10-19): the robustness model comparison -- the registered instrument under another model, one execution,
    # the author's decision bound by the contract; on A3 with the embargo evidence like the fresh-sample run
    mc = rec.get("model_comparison")
    contract = rec.get("execution_contract") or {}
    mc_c = contract.get("model_comparison")
    # Codex §4f round 2 (#1): the model and the comparison block are read from the fingerprint-bound contract as well as
    # from the record's display fields; any disagreement refuses, and either one naming a non-registered model or a
    # comparison makes the run a comparison
    registered_id = ins.MODELS[sc.REGISTERED_MODEL_KEY]
    if contract.get("model_id") and rec.get("model_key") and ins.MODELS.get(rec.get("model_key")) != contract.get("model_id"):
        raise EvalError("the run record's model key does not name the model its execution contract binds")
    non_registered = (rec.get("model_key") not in (None, sc.REGISTERED_MODEL_KEY)
                      or contract.get("model_id") not in (None, registered_id))
    if status == sc.MODEL_COMPARISON_STATUS or mc or mc_c or non_registered:
        if status != sc.MODEL_COMPARISON_STATUS:
            raise EvalError(f"a reportable run under a non-registered model, or one carrying a model-comparison block, "
                            f"must be recorded as {sc.MODEL_COMPARISON_STATUS!r}, not {status!r} (specification §4f)")
        if ident["name"] != "v1.0":
            raise EvalError("the model comparison runs the registered instrument v1.0 only (specification §4f)")
        if not non_registered:
            raise EvalError("a model-comparison run must use a model other than the registered one (specification §4f)")
        if rec.get("model_key") not in sc.MODEL_COMPARISON_ROSTER:
            raise EvalError(f"{rec.get('model_key')!r} is not in the declared comparison roster (specification §4f)")
        if int(rec.get("repeats") or 0) != sc.model_comparison_repeats(rec.get("model_key")):
            raise EvalError(f"a model-comparison run of {rec.get('model_key')!r} has k = "
                            f"{sc.model_comparison_repeats(rec.get('model_key'))} (specification §4f; §4m for its table)")
        if not (isinstance(mc, dict) and (mc.get("decision") or "").strip()
                and mc.get("authority") == sc.MODEL_COMPARISON_AUTHORITY_OF.get(rec.get("model_key"))):
            raise EvalError("a model-comparison run must carry the author's recorded decision under its declaring section (§4f / §4g)")
        if mc_c != mc:
            raise EvalError("the run record's model-comparison block is not the one its execution contract binds")
        if manifest_kind == sc.A3_KIND and not ((rec.get("a3_embargo") or {}).get("finalised")):
            raise EvalError("an A3 run must carry the embargo evidence (finalised reference) in its record")
        if manifest_kind not in (sc.A3_KIND, "A2_sample_manifest", None):
            raise EvalError(f"the model comparison is declared on the A2 sample and the A3 census only, not {manifest_kind!r}")
        return sc.MODEL_COMPARISON_STATUS
    # §4e(a) (amendment of 2026-11-05): the language arm -- the registered instrument and model, k = 3, on the stored
    # translations of the German A2 paragraphs; the override bound by the contract is what makes the status, and the
    # status exists only with it
    ti, ti_c = rec.get("translated_input"), contract.get("translated_input")
    if status == sc.TRANSLATED_STATUS or ti or ti_c:
        if status != sc.TRANSLATED_STATUS:
            raise EvalError(f"a run carrying a translated-input block must be recorded as {sc.TRANSLATED_STATUS!r}, "
                            f"not {status!r} (specification §4e(a))")
        if not isinstance(ti, dict) or ti_c != ti:
            raise EvalError("the run record's translated-input block is missing or is not the one its execution contract binds")
        if ident["name"] != "v1.0" or ident["retry_policy_version"] != "2":
            raise EvalError("the translated-input arm runs the registered instrument v1.0 under retry policy v2 only")
        if manifest_kind not in ("A2_sample_manifest", None):
            raise EvalError(f"the translated-input arm is declared on the registered A2 sample only, not {manifest_kind!r}")
        if int(rec.get("repeats") or 0) != sc.TRANSLATED_REPEATS:
            raise EvalError(f"the translated-input arm has k = {sc.TRANSLATED_REPEATS} (specification §4e(a) item 7)")
        if (ti.get("status") != sc.TRANSLATED_STATUS or ti.get("authority") != sc.TRANSLATED_AUTHORITY
                or ti.get("restrict_lang") != sc.TRANSLATED_LANG or ti.get("translator_model_id") != sc.TRANSLATOR_MODEL_ID
                or ti.get("translator_region") != sc.TRANSLATOR_REGION):
            raise EvalError("the translated-input block does not state the declared status, authority, language and translator")
        if any(rec.get(b_) or contract.get(b_) for b_ in ("model_comparison", "form_variant", "a3_candidate", "w3_authorisation")):
            raise EvalError("a translated-input run carries the block of another declared analysis")
        return sc.TRANSLATED_STATUS
    # §4l (5) (2026-11-06): the context-parity variant v1.0C on the author's recorded decision bound by the contract --
    # the registered model, k = 1, retry policy v2, on A2 or A3; descriptive, replaces nothing
    cv, cv_c = rec.get("context_variant"), contract.get("context_variant")
    if status == sc.CONTEXT_STATUS or cv or cv_c or ident["name"] == sc.CONTEXT_VARIANT:
        if status != sc.CONTEXT_STATUS:
            raise EvalError(f"a §4l context run must be recorded as {sc.CONTEXT_STATUS!r}, not {status!r}")
        if ident["name"] != sc.CONTEXT_VARIANT:
            raise EvalError(f"§4l (5) runs {sc.CONTEXT_VARIANT} only")
        if not (isinstance(cv, dict) and (cv.get("decision") or "").strip() and cv.get("authority") == sc.CONTEXT_AUTHORITY
                and cv.get("configuration") == sc.CONTEXT_VARIANT and cv.get("context_digest")):
            raise EvalError("a §4l context run must carry the author's recorded decision and the bound context digest")
        if cv_c != cv:
            raise EvalError("the run record's §4l block is not the one its execution contract binds")
        c_sample = "A3" if manifest_kind == sc.A3_KIND else "A2" if manifest_kind in ("A2_sample_manifest", None) else None
        if c_sample is None or cv.get("sample") != c_sample or cv.get("comparator") != sc.CONTEXT_COMPARATORS[c_sample]:
            raise EvalError(f"a §4l context run is declared on A2 or A3 against its declared comparator ({manifest_kind!r})")
        if int(rec.get("repeats") or 0) != sc.CONTEXT_REPEATS:
            raise EvalError(f"§4l fixes k = {sc.CONTEXT_REPEATS}")
        if c_sample == "A3" and not ((rec.get("a3_embargo") or {}).get("finalised")):
            raise EvalError("an A3 run must carry the embargo evidence (finalised reference) in its record")
        if any(rec.get(b_) or contract.get(b_) for b_ in ("model_comparison", "form_variant", "a3_candidate",
                                                          "w3_authorisation", "translated_input")):
            raise EvalError("a §4l context run carries the block of another declared analysis")
        return sc.CONTEXT_STATUS
    # §4j (2026-10-30): the mandatory-form variant v1.0F and its comparator v1.0r3, on the author's recorded decision
    # bound by the contract -- A2 (k = 1) and, for v1.0F only, the A3 census (k = 3); descriptive, replaces nothing
    form, form_c = rec.get("form_variant"), contract.get("form_variant")
    if status == sc.FORM_STATUS or form or form_c or ident["name"] == sc.FORM_VARIANT:
        if status != sc.FORM_STATUS:
            raise EvalError(f"a §4j run must be recorded as {sc.FORM_STATUS!r}, not {status!r}")
        if ident["name"] not in sc.FORM_INSTRUMENTS or ident["retry_policy_version"] != "3":
            raise EvalError(f"§4j runs {list(sc.FORM_INSTRUMENTS)} under retry policy v3 only")
        if not (isinstance(form, dict) and (form.get("decision") or "").strip() and form.get("authority") == sc.FORM_AUTHORITY
                and form.get("configuration") == ident["name"]):
            raise EvalError("a §4j run must carry the author's recorded decision under §4j for its own configuration")
        if form_c != form:
            raise EvalError("the run record's §4j block is not the one its execution contract binds")
        f_sample = "A3" if manifest_kind == sc.A3_KIND else "A2" if manifest_kind in ("A2_sample_manifest", None) else None
        if f_sample is None or form.get("sample") != f_sample:
            raise EvalError(f"a reportable §4j run is declared on A2 or A3, and its block must name that sample ({manifest_kind!r})")
        if f_sample == "A3" and ident["name"] != sc.FORM_VARIANT:
            raise EvalError("on the A3 census §4j runs v1.0F only")
        if int(rec.get("repeats") or 0) != sc.FORM_REPEATS[f_sample]:
            raise EvalError(f"§4j fixes k = {sc.FORM_REPEATS[f_sample]} on {f_sample}")
        if f_sample == "A3" and not ((rec.get("a3_embargo") or {}).get("finalised")):
            raise EvalError("an A3 run must carry the embargo evidence (finalised reference) in its record")
        return sc.FORM_STATUS
    if manifest_kind == sc.A3_KIND:
        cand, cand_c = rec.get("a3_candidate"), contract.get("a3_candidate")
        if ident["name"] == "v1.0":
            if "a3_candidate" in rec or "a3_candidate" in contract:
                raise EvalError("a v1.0 run on the A3 census carries no candidate block (specification §4b)")
        elif ident["name"] == sc.A3_CANDIDATE:
            # §4b (2026-10-26): the one frozen candidate, on the author's recorded decision bound by the contract
            if not (isinstance(cand, dict) and (cand.get("decision") or "").strip() and cand.get("authority") == sc.A3_CANDIDATE_AUTHORITY
                    and cand.get("configuration") == sc.A3_CANDIDATE and cand.get("baseline_run") == sc.A3_BASELINE_RUN):
                raise EvalError("a v1.0r3 run on the A3 census must carry the author's recorded decision under §4b "
                                "(configuration, authority, baseline run)")
            if cand_c != cand:
                raise EvalError("the run record's candidate block is not the one its execution contract binds")
        else:
            raise EvalError("only the registered instrument v1.0 and the frozen candidate v1.0r3 are declared for the "
                            "A3 census (specification §4b)")
        if status != sc.A3_STATUS:
            raise EvalError(f"an A3 run must be recorded as {sc.A3_STATUS!r}, not {status!r}")
        if not ((rec.get("a3_embargo") or {}).get("finalised")):
            raise EvalError("an A3 run must carry the embargo evidence (finalised reference) in its record")
        return sc.A3_STATUS
    if status == sc.A3_STATUS:
        raise EvalError("the status 'fresh-sample evaluation' belongs to an A3 sample manifest only")
    if ident["name"] == "v1.0":
        if status not in (None, "registered primary"):
            raise EvalError(f"a reportable v1.0 run cannot carry analysis_status {status!r}")
        return "registered primary"
    auth = rec.get("w3_authorisation")
    if status != "exploratory reused A2" or not auth:
        raise EvalError("a reportable run of a revised instrument must be recorded as 'exploratory reused A2' with its "
                        "W3 authorisation block")
    if (rec.get("execution_contract") or {}).get("w3_authorisation") != auth:
        raise EvalError("the run record's W3 authorisation block is not the one its execution contract binds")
    return status


def _git_last_change(path: Path) -> tuple[str | None, str | None]:
    """(ISO committer date of the last commit that touched `path`, sha256 of the file at HEAD), or (None, None)
    when the file has no history. The one place the evaluator asks git; tests replace it."""
    import subprocess
    try:
        rel = str(path.resolve().relative_to(ROOT))
        log = subprocess.run(["git", "log", "-1", "--format=%cI", "--", rel], cwd=str(ROOT),
                             capture_output=True, text=True, timeout=10)
        iso = log.stdout.strip()
        if log.returncode != 0 or not iso:
            return None, None
        show = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=str(ROOT), capture_output=True, timeout=10)
        if show.returncode != 0:
            return iso, None
        return iso, hashlib.sha256(show.stdout).hexdigest()
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, None


GIT_LAST_CHANGE = _git_last_change


def verify_a3_evidence(rec: dict, man: dict, gold1: Path, gold2: Path, calls_path: Path) -> dict:
    """The A3 embargo evidence (specification §4b): the run record and its execution contract carry the same
    embargo block, naming this listing and finalised reference digests equal to the files evaluated; the
    journal's start event carries the same block; and every reference file was committed, unchanged since, before
    the run's first call. Returns the evidence for the metrics."""
    emb = rec.get("a3_embargo")
    if not isinstance(emb, dict) or emb.get("touches_a3") is not True or not isinstance(emb.get("finalised"), dict):
        raise EvalError("the run record carries no A3 embargo evidence (touches_a3 with finalised reference digests)")
    if emb.get("a3_manifest_digest") != man.get("digest"):
        raise EvalError("the embargo evidence names another A3 listing than the one evaluated")
    if emb.get("paragraphs_touched") != len(man.get("paragraphs") or []):
        raise EvalError("the embargo evidence does not cover every paragraph of the A3 listing")
    if (rec.get("execution_contract") or {}).get("a3_embargo") != emb:
        raise EvalError("the run record's embargo block is not the one its execution contract binds")
    for gp in (gold1, gold2):
        want = emb["finalised"].get(gp.name)
        if not want or sha256_file(gp) != want:
            raise EvalError(f"{gp.name} is not the finalised reference file the embargo recorded (not named, or digest differs)")
    try:
        with calls_path.open(encoding="utf-8") as f:
            first = json.loads(f.readline())
    except (OSError, ValueError) as exc:
        raise EvalError(f"the calls journal cannot be read ({exc})") from None
    if first.get("type") != "run_started" or first.get("a3_embargo") != emb:
        raise EvalError("the journal's run_started event does not carry the embargo evidence of the run record")
    first_call = rec.get("first_call_at")
    if not first_call:
        raise EvalError("the run record carries no first_call_at")
    out = {"finalised": dict(emb["finalised"]), "first_call_at": first_call, "committed": {}}
    for gp in (gold1, gold2):
        iso, at_head = GIT_LAST_CHANGE(gp)
        if not iso:
            raise EvalError(f"{gp.name} has no git history; the A3 reference must be committed before the run")
        if at_head != sha256_file(gp):
            raise EvalError(f"{gp.name} differs from its committed content")
        if sc.parse_datetime(iso) > sc.parse_datetime(first_call):
            raise EvalError(f"{gp.name} was last changed in git at {iso}, after the run's first call at {first_call}")
        out["committed"][gp.name] = iso
    return out


def canonical_digest(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def verify_form_development(rec: dict) -> None:
    """Codex §4j round 2 (#2): a §4j development record is bound to its execution evidence -- the contract digests to the
    recorded fingerprint, the journal's run_started event carries that fingerprint and the same §4j block, the decision
    digest matches the decision, and every journalled gate verifies (including the module digest it certified)."""
    contract = rec.get("execution_contract") or {}
    fp = hashlib.sha256(json.dumps(contract, sort_keys=True).encode("utf-8")).hexdigest()
    if fp != rec.get("execution_fingerprint"):
        raise EvalError("the §4j development record's execution contract does not digest to its recorded fingerprint")
    form = rec.get("form_variant") or {}
    if hashlib.sha256((form.get("decision") or "").encode("utf-8")).hexdigest() != form.get("decision_sha256"):
        raise EvalError("the §4j decision digest does not match the decision")
    calls = RUNS / f"{rec.get('run_id')}_calls.jsonl"
    try:
        first = json.loads(calls.read_text(encoding="utf-8").splitlines()[0])
    except (OSError, ValueError, IndexError) as exc:
        raise EvalError(f"the §4j development run's journal cannot be read ({exc})") from None
    if first.get("type") != "run_started" or first.get("fingerprint") != fp or first.get("form_variant") != form:
        raise EvalError("the journal's run_started event does not carry this record's fingerprint and §4j block")
    verify_gate(rec)


def verify_gate(rec: dict, term_rows: list[dict] | None = None, pred_rows: list[dict] | None = None,
                paras: list[dict] | None = None) -> list[dict]:
    """A reportable evaluation does not trust `reportable: true` (Codex W1 rounds 5-7): EVERY gate the
    run journalled must exist, match its recorded digest, be CERTIFIED, and certify this run's model
    under this run's instrument (tool config, system prompt, codebook digests, all of which the run
    record must carry); and every standing terminal must have been resolved under one of those gates
    -- a later certified gate cannot qualify labels produced under an earlier, uncertified one."""
    pf = rec.get("preflight") or {}
    gates = pf.get("gates")
    if not isinstance(gates, list) or not gates:
        raise EvalError("the run record lists no journalled gates; a reportable run journals every gate it ran")
    want = {"tool_config_sha256": rec.get("tool_config_sha256"),
            "system_prompt_sha256": (rec.get("prompt") or {}).get("sha256"),
            "codebook_sha256": (rec.get("codebook") or {}).get("sha256")}
    missing = [k_ for k_, v in want.items() if not v]
    if missing:
        raise EvalError(f"the run record carries no instrument digest(s) {missing}; a reportable run names its instrument")
    # the gate's own code must be the code the run's execution contract names (Codex W1 round 8, #3)
    contract = rec.get("execution_contract") or {}
    code_want = {"preflight_sha256": contract.get("preflight_sha256"), "instrument_sha256": contract.get("instrument_sha256")}
    if contract.get("adapter_sha256"):            # §4g: an external model's adapter is bound like the gate's own code
        code_want["adapter_sha256"] = contract["adapter_sha256"]
    if not all(code_want.values()):
        raise EvalError("the run record's execution contract names no gate/instrument code digests; a reportable run carries them")
    docs = []
    for g in gates:
        art = g.get("artefact")
        gp = Path(art) if art and Path(art).is_absolute() else ROOT / str(art)
        if not art or not gp.exists() or sha256_file(gp) != g.get("sha256"):
            raise EvalError("a gate artefact the run record names is missing or its digest differs")
        d = json.loads(gp.read_text(encoding="utf-8"))
        if not d.get("certified") or not g.get("certified"):
            raise EvalError(f"a gate of this run did not certify ({d.get('certification_status')}); the run is not reportable")
        if d.get("models") != {rec.get("model_key"): rec.get("model_id")}:
            raise EvalError(f"the gate certified {d.get('models')}, not this run's model {rec.get('model_key')}={rec.get('model_id')}")
        for k_, v in want.items():
            if d.get(k_) != v:
                raise EvalError(f"the gate was certified under another instrument ({k_} differs from the run record)")
        prov = d.get("provenance") or {}
        for k_, v in code_want.items():
            if prov.get(k_) != v:
                raise EvalError(f"the gate was produced by other code than the run's execution contract names ({k_})")
        # W3 (2026-10-14): a run under a v1.1 variant must have been certified under that variant and that
        # variant module; a v1.0 run must not carry a v1.1 digest
        if "instrument_version" in contract:
            want_v = contract.get("instrument_version")
            got_v = prov.get("instrument_version") or "v1.0"
            if (want_v == "1.0") != (got_v == "v1.0") or (want_v != "1.0" and got_v != f"v{want_v}"):
                raise EvalError(f"the gate certified instrument {got_v!r}, the run's contract names {want_v!r}")
            if contract.get("instrument_v11_sha256") != prov.get("instrument_v11_sha256"):
                raise EvalError("the gate's instrument_v11.py digest is not the one the run's execution contract names")
            if contract.get("instrument_v10r3_sha256") != prov.get("instrument_v10r3_sha256"):
                raise EvalError("the gate's instrument_v10r3.py digest is not the one the run's execution contract names")
            if contract.get("instrument_v10f_sha256") != prov.get("instrument_v10f_sha256"):
                raise EvalError("the gate's instrument_v10f.py digest is not the one the run's execution contract names")
            if contract.get("instrument_v10c_sha256") != prov.get("instrument_v10c_sha256"):
                raise EvalError("the gate's instrument_v10c.py digest is not the one the run's execution contract names")
        docs.append(d)
    if not pf.get("all_certified"):
        raise EvalError("the run record does not claim that every gate certified")
    if term_rows is not None:
        shas = {g["sha256"] for g in gates}
        loose = [r for r in term_rows if r.get("outcome") in ("technologies_present", "off_topic") and r.get("gate") not in shas]
        if loose:
            raise EvalError(f"{len(loose)} label(s) were resolved under a gate that is not one of the run's journalled certified gates")
        # the summary and the standing terminals must be what the hash-verified journal says (round 8, #1)
        calls = next((m["path"] for n, m in (rec.get("files") or {}).items() if n.endswith("_calls.jsonl")), None)
        if calls is None:
            raise EvalError("the run record names no calls journal")
        cp = Path(calls) if Path(calls).is_absolute() else ROOT / calls
        try:
            rx.verify_journal_binding(rec, term_rows, cp, pred_rows=pred_rows,
                                      rates=rx.PRICES_PER_M.get(rec.get("model_key")), paras=paras)
        except ValueError as exc:
            raise EvalError(f"journal reconciliation: {exc}") from None
    return docs


def verify_frame_handshake(man: dict) -> dict:
    """The sample contract for a reportable evaluation (Codex W1 rounds 1–2, #13/#1): delegated to
    `sample_contract.verify_sample`, the same validator the runner calls before extraction, with this
    module's FRAME_MANIFEST / REGISTRY_FILE so a fixture can redirect them."""
    try:
        return sc.verify_sample(man, frame_path=FRAME_MANIFEST, registry_path=REGISTRY_FILE)
    except sc.ContractError as exc:
        raise EvalError(str(exc)) from None


def earliest_registered_coding(pass1_path: Path, registered_ids: set[str]) -> str | None:
    """The earliest coded_at over EVERY row of the supplied reference file that belongs to the registered A2
    sample, read before any restriction is applied, so a restricted evaluation cannot hide an earlier sitting.
    Every such row must carry a parseable date; otherwise the seal chronology cannot be checked and the
    evaluation refuses (Codex Loom round 3, #2)."""
    rows = [r for r in read_csv(Path(pass1_path)) if r["paragraph_id"] in registered_ids]
    blank = [r["paragraph_id"] for r in rows if not str(r.get("coded_at") or "").strip()]
    if blank:
        raise EvalError(f"{len(blank)} reference row(s) of registered A2 paragraphs carry no coded_at date "
                        f"({blank[:4]}); the seal chronology cannot be checked")
    return _earliest_coding(rows)


def _earliest_coding(rows: list[dict]) -> str | None:
    """The earliest `coded_at` date among label rows, ISO; None when no row carries a date."""
    dates = []
    for r in rows:
        v = str(r.get("coded_at") or "").strip()
        if v:
            try:
                dates.append(sc.parse_date(v))
            except sc.ContractError as exc:
                raise EvalError(f"reference row {r.get('paragraph_id')}: {exc}") from None
    return min(dates).isoformat() if dates else None


WASHOUT_DAYS_REGISTERED = 14   # specification L22, fixed 2026-09-06 before any A2 label existed


def bind_reliability_inputs(a, inp: dict, paths: list, rows: list, *, washout_days: int = WASHOUT_DAYS_REGISTERED,
                            deviation: str | None = None) -> dict:
    """Bind the four reliability files to their finalised sessions and to the route.

    Outside development, provenance is resolved through the finalisation records beside the reference
    (`sessions.csv`): each file is identified by its digest, both files of a coding must belong to the same
    session, and coding B's session manifest is read from the labels directory, never from the input path. A
    session manifest with a `recode` block is a registered re-code: it can be evaluated only under the route it
    names and only against the reference session it names (route B), and route B accepts nothing else. Route B
    further requires coding A to be the reference files and the re-code's first coding date to lie at least
    `washout_days` after the reference's last. In development (fixtures) the session is taken from the file name
    and the manifest from beside the file. Returns the provenance block."""
    p1a, p2a, p1b, p2b = (Path(x) for x in paths)
    r1a, r2a, r1b, r2b = rows
    prov = {"route": a.reliability_route, "files": {}}
    for tag, path in (("pass1_a", p1a), ("pass2_a", p2a), ("pass1_b", p1b), ("pass2_b", p2b)):
        prov["files"][tag] = {"path": str(path), "sha256": sha256_file(path)}
    gold_dir = Path(a.gold_pass1).resolve().parent
    if not a.dev:
        sessions = gold_dir / "sessions.csv"
        if not sessions.exists():
            raise EvalError("reliability inputs must be finalised sessions; no sessions.csv beside the reference")
        by_digest = {r["labels_sha256"]: r["session"] for r in read_csv(sessions)}
        def session_of(path: Path) -> str:
            d = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            if d not in by_digest:
                raise EvalError(f"reliability input {path.name} is not a finalised label file recorded in sessions.csv "
                                f"(no record carries its digest)")
            return by_digest[d]
        s1a, s2a, s1b, s2b = (session_of(p) for p in (p1a, p2a, p1b, p2b))
        for tag, sess, want in (("pass1_a", s1a, "_pass1"), ("pass2_a", s2a, "_pass2"), ("pass1_b", s1b, "_pass1"), ("pass2_b", s2b, "_pass2")):
            if not sess.endswith(want):
                raise EvalError(f"{tag} is recorded as session {sess!r}, not a {want[1:]} file")
        base_a, base_b = s1a.rsplit("_pass", 1)[0], s1b.rsplit("_pass", 1)[0]
        if s2a.rsplit("_pass", 1)[0] != base_a or s2b.rsplit("_pass", 1)[0] != base_b:
            raise EvalError(f"the two files of a coding belong to different sessions (A: {s1a}/{s2a}; B: {s1b}/{s2b})")
        man_b_path = gold_dir / f"{base_b}_manifest.json"
        if not man_b_path.exists():
            raise EvalError(f"no session manifest for coding B ({base_b}) in the labels directory; provenance is missing, "
                            f"not optional")
    else:
        base_a = Path(a.gold_pass1).name.split("_pass")[0]
        base_b = p1b.name.split("_pass")[0]
        man_b_path = p1b.resolve().parent / f"{base_b}_manifest.json"
    man_b = json.loads(man_b_path.read_text(encoding="utf-8")) if man_b_path.exists() else None
    recode = (man_b or {}).get("recode")
    prov["coding_a_session"] = base_a
    prov["coding_b_session"] = {"base": base_b, "manifest": str(man_b_path) if man_b else None, "recode": recode}
    if recode is not None and recode.get("route") != a.reliability_route:
        raise EvalError(f"coding B is the registered re-code session {base_b} (route {recode.get('route')}); it cannot be "
                        f"evaluated under route {a.reliability_route}")
    if a.reliability_route == "B":
        if recode is None:
            raise EvalError(f"route B: coding B ({base_b}) carries no `recode` block in its session manifest; a re-code is "
                            f"generated by recode_sheet.py and is the only admissible coding B")
        if recode.get("of_session") != base_a:
            raise EvalError(f"route B: the re-code {base_b} is registered for session {recode.get('of_session')!r}, "
                            f"not for the reference session {base_a!r}")
        if sha256_file(p1a) != sha256_file(Path(a.gold_pass1)) or sha256_file(p2a) != sha256_file(Path(a.gold_pass2)):
            raise EvalError("route B: coding A must be the reference label files themselves (digests differ from --gold-pass1/2)")
        if sha256_file(p1b) == sha256_file(p1a) or sha256_file(p2b) == sha256_file(p2a):
            raise EvalError("route B: the re-code files are identical to the reference files; a re-code is a separate session")
    def _dates(rs):
        ds = []
        for r in rs:
            v = str(r.get("coded_at") or "").strip()
            if v:
                try:
                    ds.append(sc.parse_date(v))
                except sc.ContractError as exc:
                    raise EvalError(str(exc)) from None
        return ds
    da, db = _dates(r1a) + _dates(r2a), _dates(r1b) + _dates(r2b)
    prov["coding_a"] = {"first": min(da).isoformat(), "last": max(da).isoformat()} if da else None
    prov["coding_b"] = {"first": min(db).isoformat(), "last": max(db).isoformat()} if db else None
    if a.reliability_route == "B":
        if not da or not db:
            raise EvalError("route B: both codings must carry coded_at dates for the washout check")
        gap = (min(db) - max(da)).days
        prov["washout_days"] = gap
        prov["washout_days_registered"] = WASHOUT_DAYS_REGISTERED
        prov["washout_days_applied"] = washout_days
        if washout_days != WASHOUT_DAYS_REGISTERED:
            # a shortened washout is a disclosed deviation from the registered rule, never a silent default:
            # it is accepted only with a written note, and the note travels into the metrics and the tables
            if not deviation or not deviation.strip():
                raise EvalError(f"route B: --washout-days {washout_days} differs from the registered {WASHOUT_DAYS_REGISTERED}; "
                                f"a --washout-deviation note (who decided, when, why) is required")
            prov["washout_deviation"] = deviation.strip()
        if gap < washout_days:
            raise EvalError(f"route B: the re-code began {gap} day(s) after the reference's last coding; the applied washout is {washout_days} days"
                            + ("" if washout_days == WASHOUT_DAYS_REGISTERED else f" (registered {WASHOUT_DAYS_REGISTERED})"))
    return prov


def load_inputs(a) -> dict:
    rec = json.loads(Path(a.run_record).read_text(encoding="utf-8"))
    if not a.dev and (a.restrict_split or (rec.get("manifest") or {}).get("restrict_split")):
        raise EvalError("a reportable evaluation covers the whole registered sample; --restrict-split is a "
                        "development option (a filtered sample keeps the full sample's weights)")
    if not rec.get("reportable") and not a.dev:
        raise EvalError(f"run {rec['run_id']} is not reportable (dev={rec.get('dev')}, "
                        f"certified={rec.get('preflight', {}).get('certified')}); pass --dev for "
                        f"development tables only")
    for name, meta in rec["files"].items():
        p = ROOT / meta["path"] if not Path(meta["path"]).is_absolute() else Path(meta["path"])
        if not p.exists() or sha256_file(p) != meta["sha256"]:
            raise EvalError(f"{name} is missing or its digest differs from the run record")
    man = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    body = {k: v for k, v in man.items() if k != "digest"}
    computed = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False).encode("utf-8")).hexdigest()
    if man.get("digest") and man["digest"] != computed:
        raise EvalError("the manifest's content does not match its stored digest")
    if computed != rec["manifest"]["sha256"]:
        raise EvalError("this is not the manifest the run was executed on (digest differs from the run record)")
    if (a.restrict_split or None) != (rec["manifest"].get("restrict_split") or None):
        raise EvalError(f"--restrict-split {a.restrict_split!r} differs from the run's {rec['manifest'].get('restrict_split')!r}")
    paras = man["paragraphs"]
    if a.restrict_split:
        paras = [p for p in paras if p.get("split") == a.restrict_split]
    # §4e(a): a run bound to translated inputs is scored on the German A2 paragraphs only, with their original design
    # weights, against the unchanged reference -- and only after the author's translation check is sealed and committed
    ti = (rec.get("execution_contract") or {}).get("translated_input")
    translated_info = None
    if ti or rec.get("translated_input") or rec.get("analysis_status") == sc.TRANSLATED_STATUS \
            or (rec.get("manifest") or {}).get("restrict_lang"):
        if a.dev:
            raise EvalError("a translated-input run (specification §4e(a)) is never evaluated in development mode")
        if analysis_status_of(rec, manifest_kind=man.get("kind")) != sc.TRANSLATED_STATUS or not isinstance(ti, dict):
            raise EvalError("a language restriction without the bound translated-input override is refused (specification §4e(a))")
        if (rec.get("manifest") or {}).get("restrict_lang") != ti.get("restrict_lang"):
            raise EvalError("the run record's language restriction is not the one its execution contract binds")
        paras = [p for p in paras if p.get("lang") == ti["restrict_lang"]]
        if sorted(p["paragraph_id"] for p in paras) != ti.get("paragraph_ids"):
            raise EvalError("the German paragraphs of the manifest are not the ones the translated-input contract binds")
        tpath = Path(ti.get("file") or "")
        tpath = tpath if tpath.is_absolute() else ROOT / tpath
        try:
            # the shared verification (rows, journal and committed record of the translation step); the registered texts
            # were checked by the runner, whose binding -- the file digest included -- must equal this one
            trows, binding = sc.load_translation_file(tpath, paras)
        except sc.ContractError as exc:
            raise EvalError(f"translated input: {exc}") from None
        if binding != ti:
            raise EvalError("the translation file is not the one the run's execution contract binds (file digest or "
                            "per-paragraph hashes differ)")
        try:
            check = sc.verify_check_seal(binding["file_sha256"], trows)
        except sc.ContractError as exc:
            raise EvalError(f"translation check: {exc}; no agreement figure of the language arm is computed before the "
                            f"author's check file exists and its digest is committed (specification §4e(a) item 6)") from None
        translated_info = {"translated_input": ti,
                           "translation_check": {k_: check.get(k_) for k_ in ("digest", "check_sha256", "translation_file_sha256",
                                                                             "summary", "sealed_at", "commitment",
                                                                             "translated_runs_before_seal")}}
    if len(paras) != rec["manifest"]["n_paragraphs"]:
        raise EvalError("the resolved selection differs in size from the run record")
    fresh = man.get("kind") == sc.A3_KIND       # the fresh-sample census (specification §4b)
    if man.get("kind") == sc.FRAME_COMPLETION_KIND or rec.get("analysis_status") == sc.FRAME_COMPLETION_STATUS:
        raise EvalError("a frame-completion run has no reference and is never scored (specification §4d)")
    if not a.dev:
        # reportable: the registered A2 sample, design-weighted, hand-shaken with the frozen frame and the
        # registry head; or the A3 census under its own contract; nothing is defaulted (Codex W1 round 1, #13)
        if fresh:
            try:
                sc.verify_a3_sample(man, frame_path=FRAME_MANIFEST, registry_path=REGISTRY_FILE)
            except sc.ContractError as exc:
                raise EvalError(str(exc)) from None
            if getattr(a, "reliability", None):
                raise EvalError("table 9 (reliability) is not applicable to the A3 census: no re-code exists (specification §4b)")
        else:
            if man.get("kind") != "A2_sample_manifest":
                raise EvalError(f"a reportable evaluation needs an A2 or A3 sample manifest (kind={man.get('kind')!r}); "
                                f"pass --dev for development tables")
            verify_frame_handshake(man)        # kind, frame, registry, membership, design, weights
    for p in paras:
        if a.dev:
            p.setdefault("weight", 1.0)
        p.setdefault("exposure", "unknown")
    pids = [p["paragraph_id"] for p in paras]
    pset = set(pids)
    if a.dev and Path(sc.A3_SAMPLE_MANIFEST).exists():
        # A3 is never inspected in development mode (specification §4b)
        try:
            a3_ids = {q.get("paragraph_id") for q in (json.loads(Path(sc.A3_SAMPLE_MANIFEST).read_text(encoding="utf-8")).get("paragraphs") or [])}
        except (OSError, ValueError, AttributeError) as exc:
            raise EvalError(f"the A3 listing cannot be read ({exc}); a development evaluation cannot establish that it touches no A3 paragraph") from None
        if pset & a3_ids:
            raise EvalError(f"{len(pset & a3_ids)} evaluated paragraph(s) belong to the A3 census; A3 is never evaluated in development mode (specification §4b)")
    if not a.dev:
        # every journalled gate certified, every label resolved under one of them -- checked before the
        # reference is even opened, so a run that was never certified is refused as such
        _files = {n: (ROOT / m["path"] if not Path(m["path"]).is_absolute() else Path(m["path"])) for n, m in rec["files"].items()}
        _term = [r for r in read_csv(next(p for n, p in _files.items() if n.endswith("_terminal.csv"))) if r["paragraph_id"] in pset]
        _pred = list(read_csv(next(p for n, p in _files.items() if n.endswith("_predictions.csv"))))
        verify_gate(rec, _term, _pred, paras)
    g1 = [r for r in read_csv(Path(a.gold_pass1)) if r["paragraph_id"] in pset]
    g2 = [r for r in read_csv(Path(a.gold_pass2)) if r["paragraph_id"] in pset]
    # ---- the blind threshold seal (Codex Loom rounds 1-2). Reportable: the seal must exist, be intact and
    # committed unchanged, name this sample, equal --threshold, and its commit must not postdate the first
    # coded reference row. Development: the same binding applies as soon as any evaluated identity belongs to
    # the registered A2 sample, so an A2 run cannot be inspected under an unsealed or different threshold.
    seal_info = None
    earliest = None
    a3_evidence = None
    registered = json.loads(Path(sc.SAMPLE_MANIFEST).read_text(encoding="utf-8")) if Path(sc.SAMPLE_MANIFEST).exists() else None
    registered_ids = {q.get("paragraph_id") for q in (registered or {}).get("paragraphs") or []}
    if fresh and not a.dev:
        # §4b: the threshold is declared for A3, not sealed (the A2 seal covers the A2 sample only); the embargo
        # evidence binds the evaluated reference to the files that released the run
        if a.threshold != sc.A3_THRESHOLD:
            raise EvalError(f"the A3 census is evaluated under the declared threshold s = {sc.A3_THRESHOLD} "
                            f"({sc.A3_AUTHORITY}); --threshold {a.threshold} is not it")
        _calls = next(p for n, p in _files.items() if n.endswith("_calls.jsonl"))
        a3_evidence = verify_a3_evidence(rec, man, Path(a.gold_pass1), Path(a.gold_pass2), _calls)
    elif not a.dev:
        earliest = earliest_registered_coding(Path(a.gold_pass1), registered_ids or pset)
        try:
            seal_info = sc.verify_threshold_seal(a.threshold, man, earliest_coding=earliest)
        except sc.ContractError as exc:
            raise EvalError(str(exc)) from None
    elif registered is not None and (pset & registered_ids):
        earliest = earliest_registered_coding(Path(a.gold_pass1), registered_ids)
        try:
            seal_info = sc.verify_threshold_seal(a.threshold, registered, earliest_coding=earliest)
        except sc.ContractError as exc:
            raise EvalError(f"development evaluation over registered A2 paragraphs: {exc}") from None
    # coverage: every manifest paragraph has a pass-1 row; pass-2 keys == pass-1 implied keys
    have1 = {r["paragraph_id"] for r in g1}
    missing = sorted(pset - have1)
    if missing:
        raise EvalError(f"{len(missing)} manifest paragraph(s) have no gold pass-1 row: {missing[:5]}")
    keys1 = {(r["paragraph_id"], c[len("tech_"):]) for r in g1 for c in r
             if c.startswith("tech_") and str(r[c]).strip() == "1"}
    keys2 = {(r["paragraph_id"], r["technology"]) for r in g2}
    if keys1 != keys2:
        raise EvalError(f"gold pass-1 keys and pass-2 rows differ: only pass1 {sorted(keys1 - keys2)[:4]}, "
                        f"only pass2 {sorted(keys2 - keys1)[:4]}")
    # the reference is bound to its provenance: the label files' digests in sessions.csv, the
    # session manifest's text hashes for the evaluation paragraphs, and the pass-1 rows' own hashes
    gold_dir = Path(a.gold_pass1).resolve().parent
    sessions = gold_dir / "sessions.csv"
    if a.skip_provenance and not a.dev:
        raise EvalError("--skip-provenance is a fixture-test convenience and is only accepted with --dev")
    if not a.skip_provenance and not sessions.exists():
        raise EvalError(f"no sessions.csv beside the reference labels ({gold_dir}); a reference without its "
                        f"finalization record is not a reference")
    if sessions.exists() and not a.skip_provenance:
        recorded = {r["labels_file"]: r["labels_sha256"] for r in read_csv(sessions)}
        for gp in (Path(a.gold_pass1), Path(a.gold_pass2)):
            want = recorded.get(gp.name)
            if want is None:
                raise EvalError(f"{gp.name} is not recorded in sessions.csv; it is not a finalized label file")
            if hashlib.sha256(gp.read_bytes()).hexdigest()[:16] != want:
                raise EvalError(f"{gp.name} differs from the digest sessions.csv recorded at finalization")
    gman_path = Path(a.gold_manifest) if a.gold_manifest else (gold_dir / (Path(a.gold_pass1).name.split("_pass")[0] + "_manifest.json"))
    if not a.skip_provenance:
        if not gman_path.exists():
            raise EvalError(f"no session manifest for the reference labels at {gman_path.name}")
        gman = json.loads(gman_path.read_text(encoding="utf-8"))
        gh = {q["paragraph_id"]: q["text_sha256"] for q in gman["paragraphs"]}
        bad = [p["paragraph_id"] for p in paras if gh.get(p["paragraph_id"]) != p["text_sha256"]]
        if bad:
            raise EvalError(f"{len(bad)} evaluation paragraph(s) are not, or not with the same text, in the "
                            f"reference session's manifest: {bad[:5]}")
        # the supplied files must be the passes the manifest declares (where it declares them)
        declared = {k_: v.get("labels_file") for k_, v in (gman.get("passes") or {}).items() if v.get("labels_file")}
        for k_, gp in (("pass1", Path(a.gold_pass1)), ("pass2", Path(a.gold_pass2))):
            if declared.get(k_) and declared[k_] != gp.name:
                raise EvalError(f"{gp.name} is not the {k_} file the reference manifest declares ({declared[k_]})")
        for tag, rows_ in (("pass-1", g1), ("pass-2", g2)):
            if rows_ and "text_sha256" in rows_[0]:
                bad = [r["paragraph_id"] for r in rows_ if gh.get(r["paragraph_id"]) != str(r["text_sha256"]).strip()]
                if bad:
                    raise EvalError(f"{len(bad)} gold {tag} row(s) carry a text hash that is not the manifest's: {bad[:5]}")
    files = {n: (ROOT / m["path"] if not Path(m["path"]).is_absolute() else Path(m["path"]))
             for n, m in rec["files"].items()}
    term = [r for r in read_csv(next(p for n, p in files.items() if n.endswith("_terminal.csv")))
            if r["paragraph_id"] in pset]
    pred = [r for r in read_csv(next(p for n, p in files.items() if n.endswith("_predictions.csv")))
            if r["paragraph_id"] in pset]
    k = int(rec["repeats"])
    ids = Counter((r["paragraph_id"], int(r["run_index"])) for r in term)
    want = {(pid, r) for pid in pids for r in range(1, k + 1)}
    if set(ids) != want or any(n != 1 for n in ids.values()):
        raise EvalError(f"terminal records must be exactly one per (paragraph, execution 1..{k}); "
                        f"missing {sorted(want - set(ids))[:4]}, extra/duplicate "
                        f"{sorted(set(ids) - want)[:4] or [k_ for k_, n in ids.items() if n > 1][:4]}")
    doc, subtypes, _ = ins.load_codebook()
    frame_of = {e["id"]: e["frame"] for e in doc["frame_elements"]}
    provenance = {"run_record_sha256": sha256_file(Path(a.run_record)), "run_record_files": rec.get("files"),
                  "reference": {Path(a.gold_pass1).name: sha256_file(Path(a.gold_pass1)),
                                Path(a.gold_pass2).name: sha256_file(Path(a.gold_pass2))},
                  "evaluate_sha256": sha256_file(Path(__file__).resolve())}
    if seal_info is not None:
        provenance["threshold_seal"] = {"s": seal_info["s"], "digest": seal_info["digest"], "created_at": seal_info["created_at"],
                                        "commit": seal_info["commitment"]["commit"], "committed_at": seal_info["commitment"]["committed_at"],
                                        "first_reference_coding": earliest}
    if translated_info is not None:
        provenance.update(translated_info)
    if fresh and not a.dev:
        provenance["threshold_declaration"] = {"s": sc.A3_THRESHOLD, "authority": sc.A3_AUTHORITY,
                                               "note": "declared before any A3 label existed; the A2 seal covers the A2 sample only"}
        provenance["a3_evidence"] = a3_evidence
        provenance["sample_kind"] = sc.A3_KIND
    return {"rec": rec, "manifest": man, "paras": paras, "gold1": g1, "gold2": g2, "term": term, "provenance": provenance,
            "pred": pred, "k": k, "frame_of": frame_of}


# --------------------------------------------------------------------------- #
# structures for execution 1 scoring, per article (so resamples are sums)
# --------------------------------------------------------------------------- #
def build(inp: dict) -> dict:
    paras = inp["paras"]
    by_pid = {p["paragraph_id"]: p for p in paras}
    lang = {p["paragraph_id"]: p["lang"] for p in paras}
    w = {p["paragraph_id"]: float(p["weight"]) for p in paras}
    art = {p["paragraph_id"]: p["article_id"] for p in paras}
    frame_of = inp["frame_of"]
    gold = {(r["paragraph_id"], r["technology"]): rel_from_row(r, frame_of) for r in inp["gold2"]}
    gold_off = {r["paragraph_id"] for r in inp["gold1"]
                if not any(c.startswith("tech_") and str(r[c]).strip() == "1" for c in r)}
    # predictions per execution
    pred = defaultdict(dict)   # run_index -> {(pid, tech): Rel}
    for r in inp["pred"]:
        pred[int(r["run_index"])][(r["paragraph_id"], r["technology"])] = rel_from_row(r, frame_of)
    outcome = defaultdict(dict)  # run_index -> pid -> outcome
    for r in inp["term"]:
        outcome[int(r["run_index"])][r["paragraph_id"]] = r["outcome"]
    return {"by_pid": by_pid, "lang": lang, "w": w, "art": art, "gold": gold, "gold_off": gold_off,
            "pred": pred, "outcome": outcome, "k": inp["k"], "pids": [p["paragraph_id"] for p in paras]}


def counts_execution(S: dict, pids: list[str], r: int = 1) -> dict:
    """Per-article, per-class TP/FP/FN (weighted and unweighted) for every table, for the
    given paragraphs, against execution `r` (1 = the primary execution of every reported
    table; the other executions feed only the W1 selection statistic and the stability
    section). Everything downstream is a sum over articles."""
    gold, pred1 = S["gold"], S["pred"].get(r, {})
    w, art = S["w"], S["art"]
    C = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0, 0, 0, 0]))  # table -> class -> [wTP,wFP,wFN,TP,FP,FN]
    per_art = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0, 0, 0, 0])))
    supp_rows, supp_arts = defaultdict(Counter), defaultdict(lambda: defaultdict(set))
    A_units = defaultdict(lambda: defaultdict(list))     # table -> article -> [(w, correct, gold, pred)]
    A_excluded = Counter()
    exact = defaultdict(lambda: [0.0, 0.0, 0, 0])         # article -> [w_correct, w_total, correct, total]

    def add(table, cls, a, wt, tp=0, fp=0, fn=0):
        for target in (C[table][cls], per_art[a][table][cls]):
            target[0] += wt * tp; target[1] += wt * fp; target[2] += wt * fn
            target[3] += tp; target[4] += fp; target[5] += fn

    pset = set(pids)
    keys = {k for k in gold if k[0] in pset} | {k for k in pred1 if k[0] in pset}
    for pid, tech in sorted(keys):
        a, wt = art[pid], w[pid]
        g, p = gold.get((pid, tech)), pred1.get((pid, tech))
        # technology detection
        add("technology", tech, a, wt, tp=int(g is not None and p is not None),
            fp=int(g is None and p is not None), fn=int(g is not None and p is None))
        if g is not None:
            supp_rows["technology"][tech] += 1; supp_arts["technology"][tech].add(a)
        ge = g.elements if g else frozenset(); pe = p.elements if p else frozenset()
        for e in ELEMENTS:
            add("element", e, a, wt, tp=int(e in ge and e in pe), fp=int(e not in ge and e in pe),
                fn=int(e in ge and e not in pe))
            if e in ge:
                supp_rows["element"][e] += 1; supp_arts["element"][e].add(a)
        # score B: labelled tuples (frame, value) and (target)
        gev, pev = dict(g.evals) if g else {}, dict(p.evals) if p else {}
        for fr in FRAMES:
            gv, pv = gev.get(fr), pev.get(fr)
            if gv is not None:
                supp_rows["evaluation_B"][gv] += 1; supp_arts["evaluation_B"][gv].add(a)
            if gv is not None and pv == gv:
                add("evaluation_B", gv, a, wt, tp=1)
            else:
                if gv is not None:
                    add("evaluation_B", gv, a, wt, fn=1)
                if pv is not None:
                    add("evaluation_B", pv, a, wt, fp=1)
        gt, pt = (g.target if g else None), (p.target if p else None)
        if gt is not None:
            supp_rows["comparison_B"][gt] += 1; supp_arts["comparison_B"][gt].add(a)
        if gt is not None and pt == gt:
            add("comparison_B", gt, a, wt, tp=1)
        else:
            if gt is not None:
                add("comparison_B", gt, a, wt, fn=1)
            if pt is not None:
                add("comparison_B", pt, a, wt, fp=1)
        # score A: intersection-conditional units
        if g is not None and p is not None:
            for fr, gv in gev.items():
                pv = pev.get(fr)
                if pv is None:
                    A_excluded["evaluation_A:frame_not_predicted"] += 1
                    continue
                A_units["evaluation_A"][a].append((wt, int(pv == gv), gv, pv))
                supp_rows["evaluation_A"][gv] += 1; supp_arts["evaluation_A"][gv].add(a)
                add("evaluation_A", gv, a, wt, tp=int(pv == gv), fn=int(pv != gv))
                if pv != gv:
                    add("evaluation_A", pv, a, wt, fp=1)
            A_units["comparison_A"][a].append((wt, int(pt == gt), gt, pt))
            supp_rows["comparison_A"][gt] += 1; supp_arts["comparison_A"][gt].add(a)
            add("comparison_A", gt, a, wt, tp=int(pt == gt), fn=int(pt != gt))
            if pt != gt:
                add("comparison_A", pt, a, wt, fp=1)
        elif g is not None:
            A_excluded["evaluation_A:row_not_predicted"] += len(gev)
            A_excluded["comparison_A:row_not_predicted"] += 1
        # exact match per row (secondary)
        ok = int(g is not None and p is not None and g == p)
        exact[a][0] += wt * ok; exact[a][1] += wt; exact[a][2] += ok; exact[a][3] += 1
    return {"C": C, "per_art": per_art, "supp_rows": supp_rows, "supp_arts": supp_arts,
            "A_units": A_units, "A_excluded": A_excluded, "exact": exact}


def counts_execution1(S: dict, pids: list[str]) -> dict:
    return counts_execution(S, pids, 1)


def micro(per_art, table, arts_mult: dict, weighted: bool):
    tp = fp = fn = 0.0
    for a, m in arts_mult.items():
        for cls, v in per_art[a][table].items():
            if weighted:
                tp += m * v[0]; fp += m * v[1]; fn += m * v[2]
            else:
                tp += m * v[3]; fp += m * v[4]; fn += m * v[5]
    return f1(tp, fp, fn)


def per_class(per_art, table, arts_mult: dict, weighted: bool, classes) -> dict:
    out = {}
    for cls in classes:
        tp = fp = fn = 0.0
        for a, m in arts_mult.items():
            v = per_art[a][table].get(cls)
            if v is None:
                continue
            if weighted:
                tp += m * v[0]; fp += m * v[1]; fn += m * v[2]
            else:
                tp += m * v[3]; fp += m * v[4]; fn += m * v[5]
        out[cls] = f1(tp, fp, fn)
    return out


def macro_available(pc: dict, min_classes: int = 2):
    """Macro-F1 over the defined classes. At the point estimate a dimension with fewer than two supported
    classes gets no macro-F1 (L21, min_classes=2); inside a resample the available-class macro averages
    whatever classes remain defined (L26, min_classes=1) and the defined-class count is reported beside it."""
    vals = [v for v in pc.values() if v is not None]
    return (sum(vals) / len(vals) if len(vals) >= min_classes else None), len(vals)


def accuracy_A(A_units, table, arts_mult, weighted: bool):
    num = den = 0.0
    for a, m in arts_mult.items():
        for wt, ok, _, _ in A_units[table].get(a, []):
            num += m * (wt if weighted else 1) * ok; den += m * (wt if weighted else 1)
    return None if den == 0 else num / den


# --------------------------------------------------------------------------- #
# stability (RQ3)
# --------------------------------------------------------------------------- #
def stability_per_paragraph(S: dict, pid: str) -> dict:
    k = S["k"]
    succ = [r for r in range(1, k + 1) if S["outcome"].get(r, {}).get(pid) in SUCCESS]
    fails = k - len(succ)
    pairs = [(r, s) for i, r in enumerate(succ) for s in succ[i + 1:]]
    out = {"n_success": len(succ), "n_fail": fails, "n_pairs": len(pairs), "tech": None,
           "elements": None, "evaluations": None, "comparison": None, "subtype": None, "any": None,
           "empty_union": False}
    if not pairs:
        return out
    techset = {r: frozenset(t for (p, t) in S["pred"].get(r, {}) if p == pid) for r in succ}
    out["tech"] = sum(techset[r] != techset[s] for r, s in pairs) / len(pairs)
    union = set().union(*techset.values())
    # `any` is a PARAGRAPH-level indicator per pair: the technology set or any relation differs
    any_pair = []
    for r, s in pairs:
        changed = techset[r] != techset[s]
        for tech in sorted(union):
            if S["pred"].get(r, {}).get((pid, tech)) != S["pred"].get(s, {}).get((pid, tech)):
                changed = True
        any_pair.append(changed)
    out["any"] = sum(any_pair) / len(any_pair)
    if not union:
        out["empty_union"] = True           # relation-level dimensions do not apply
        return out
    rates = defaultdict(float); n = 0
    for tech in sorted(union):
        for r, s in pairs:
            a = S["pred"].get(r, {}).get((pid, tech)); b = S["pred"].get(s, {}).get((pid, tech))
            rates["elements"] += (a.elements if a else ABSENT) != (b.elements if b else ABSENT)
            rates["evaluations"] += (a.evals if a else ABSENT) != (b.evals if b else ABSENT)
            rates["comparison"] += (a.target if a else ABSENT) != (b.target if b else ABSENT)
            rates["subtype"] += (a.subtype if a else ABSENT) != (b.subtype if b else ABSENT)
            n += 1
    for key in ("elements", "evaluations", "comparison", "subtype"):
        out[key] = rates[key] / n
    return out


# --------------------------------------------------------------------------- #
# prevalence presence (RQ4a)
# --------------------------------------------------------------------------- #
def presence(S: dict, pid: str) -> tuple[dict, dict]:
    """(gold classes present, predicted classes present at execution 1) for the paragraph:
    technology:<t>, element:<e>, eval:<frame>=<value>."""
    def classes(rels: dict) -> set:
        out = set()
        for (p, t), rel in rels.items():
            if p != pid:
                continue
            out.add(f"technology:{t}")
            out |= {f"element:{e}" for e in rel.elements}
            out |= {f"eval:{fr}={v}" for fr, v in rel.evals}
        return out
    return classes(S["gold"]), classes(S["pred"].get(1, {}))


# --------------------------------------------------------------------------- #
# reliability (aligned units, three levels)
# --------------------------------------------------------------------------- #
def krippendorff_nominal(pairs: list[tuple]):
    """Two raters, nominal. pairs: list of (value_a, value_b). Returns (alpha, raw_agreement,
    n_units) with alpha None when there is no variation (D_e == 0)."""
    n_units = len(pairs)
    if n_units == 0:
        return None, None, 0
    vals = Counter()
    disagree = 0
    for a, b in pairs:
        vals[a] += 1; vals[b] += 1
        disagree += (a != b)
    n = 2 * n_units
    Do = disagree / n_units            # each unit: 2 values, m_u - 1 = 1, o_ck per unit = 2 for c!=k pairs / 1 -> sum_{c!=k} o_ck = 2*disagree; /n = 2*disagree/(2 n_units)
    De = sum(vals[c] * vals[k] for c in vals for k in vals if c != k) / (n * (n - 1)) if n > 1 else 0
    raw = 1 - disagree / n_units
    if De == 0:
        return None, raw, n_units
    return 1 - Do / De, raw, n_units


def reliability(p1a, p2a, p1b, p2b, frame_of, designated: set[str] | None = None) -> dict:
    """Three levels on aligned units; a and b are the two coders' (pass1, pass2) label rows.
    Both coders must cover the designated subsample completely and each coder's pass-1 ticks
    must equal their pass-2 rows; a missing rating is refused, never dropped."""
    if designated is not None:
        for tag, p1 in (("coder A", p1a), ("coder B", p1b)):
            have = {r["paragraph_id"] for r in p1}
            if not designated <= have:
                raise EvalError(f"{tag} did not code {len(designated - have)} designated reliability paragraph(s): "
                                f"{sorted(designated - have)[:5]}")
        p1a = [r for r in p1a if r["paragraph_id"] in designated]; p1b = [r for r in p1b if r["paragraph_id"] in designated]
        p2a = [r for r in p2a if r["paragraph_id"] in designated]; p2b = [r for r in p2b if r["paragraph_id"] in designated]
    for tag, p1, p2 in (("coder A", p1a, p2a), ("coder B", p1b, p2b)):
        k1 = {(r["paragraph_id"], c[len("tech_"):]) for r in p1 for c in r if c.startswith("tech_") and str(r[c]).strip() == "1"}
        k2 = {(r["paragraph_id"], r["technology"]) for r in p2}
        if k1 != k2:
            raise EvalError(f"{tag}: pass-1 ticks and pass-2 rows differ ({sorted(k1 ^ k2)[:4]})")
    def ticks(rows):
        out = defaultdict(set)
        for r in rows:
            for c in r:
                if c.startswith("tech_") and str(r[c]).strip() == "1":
                    out[r["paragraph_id"]].add(c[len("tech_"):])
        return out
    ta, tb = ticks(p1a), ticks(p1b)
    pids = sorted(set(r["paragraph_id"] for r in p1a) & set(r["paragraph_id"] for r in p1b))
    techs = list(inventories()["technology"])            # the frozen inventory, not the observed ticks (Codex Loom r2 #12)
    techs += sorted({t for s_ in list(ta.values()) + list(tb.values()) for t in s_ if t not in techs})
    out = {"n_paragraphs": len(pids), "technology": {}, "elements": {}, "evaluation": {},
           "comparison_target": None, "aligned_rows": 0, "rows_only_a": 0, "rows_only_b": 0}
    art_of = lambda pid: pid.split("_")[0]
    for t in techs:
        pairs = [(int(t in ta[p]), int(t in tb[p])) for p in pids]
        al, raw, n = krippendorff_nominal(pairs)
        out["technology"][t] = {"alpha": al, "raw": raw, "n": n,
                                # support = units on which the FIRST coding (coder A, the reference) is positive;
                                # the second coding's positives are printed beside it, never summed into it
                                "support_rows": sum(a for a, _ in pairs), "support_rows_b": sum(b for _, b in pairs),
                                "support_articles": len({art_of(p) for p in pids if t in ta[p]})}
    ra = {(r["paragraph_id"], r["technology"]): rel_from_row(r, frame_of) for r in p2a if r["paragraph_id"] in pids}
    rb = {(r["paragraph_id"], r["technology"]): rel_from_row(r, frame_of) for r in p2b if r["paragraph_id"] in pids}
    aligned = sorted(set(ra) & set(rb))
    out["aligned_rows"] = len(aligned); out["rows_only_a"] = len(set(ra) - set(rb)); out["rows_only_b"] = len(set(rb) - set(ra))
    for e in ELEMENTS:
        pairs = [(int(e in ra[k].elements), int(e in rb[k].elements)) for k in aligned]
        al, raw, n = krippendorff_nominal(pairs)
        out["elements"][e] = {"alpha": al, "raw": raw, "n": n,
                              "support_rows": sum(a for a, _ in pairs), "support_rows_b": sum(b for _, b in pairs),
                              "support_articles": len({art_of(k[0]) for k in aligned if e in ra[k].elements})}
    for fr in FRAMES:
        pairs = []
        for k_ in aligned:
            ea, eb = dict(ra[k_].evals), dict(rb[k_].evals)
            if fr in ea and fr in eb:
                pairs.append((ea[fr], eb[fr]))
        al, raw, n = krippendorff_nominal(pairs)
        out["evaluation"][fr] = {"alpha": al, "raw": raw, "n": n, "support_rows": n,   # units inside the applicability mask
                                 "support_articles": len({art_of(k_[0]) for k_ in aligned if fr in dict(ra[k_].evals) and fr in dict(rb[k_].evals)}),
                                 "mask_coverage": f"{n} of {sum(1 for k_ in aligned if fr in dict(ra[k_].evals) or fr in dict(rb[k_].evals))} rows where either fired"}
    pairs = [(ra[k_].target, rb[k_].target) for k_ in aligned]
    al, raw, n = krippendorff_nominal(pairs)
    out["comparison_target"] = {"alpha": al, "raw": raw, "n": n, "support_rows": n, "support_articles": len({art_of(k_[0]) for k_ in aligned})}
    return out


# --------------------------------------------------------------------------- #
# the evaluation
# --------------------------------------------------------------------------- #
def evaluate(inp: dict, *, threshold: int, B: int, seed: int, dev: bool) -> dict:
    global INV
    INV = inventories()
    S = build(inp)
    pids = S["pids"]
    langs = sorted(set(S["lang"].values()))
    pop = {"pooled": pids, **{L: [p for p in pids if S["lang"][p] == L] for L in langs}}
    clean_all = [p for p in pids if S["by_pid"][p].get("exposure") == "clean_article"]
    if clean_all and len(clean_all) < len(pids):       # the clean-domain sensitivity, same pipeline
        pop["clean_pooled"] = clean_all
        for L in langs:
            sub_ = [p for p in clean_all if S["lang"][p] == L]
            if sub_:
                pop[f"clean_{L}"] = sub_
    cnt = counts_execution1(S, pids)
    arts_of = {name: sorted({S["art"][p] for p in ps}) for name, ps in pop.items()}
    base_mult = {name: {a: 1 for a in arts} for name, arts in arts_of.items()}

    # ---- class manifest: per table, per population, positive gold support ------------------ #
    class_manifest = {}
    for table in ("technology", "element", "evaluation_A", "evaluation_B", "comparison_A", "comparison_B"):
        class_manifest[table] = {}
        for name, ps in pop.items():
            sub = counts_execution1(S, ps) if name != "pooled" else cnt
            classes = {c: {"rows": n, "articles": len(sub["supp_arts"][table][c])}
                       for c, n in sub["supp_rows"][table].items() if n > 0}
            class_manifest[table][name] = classes
        if len(langs) >= 2:
            common = set.intersection(*(set(class_manifest[table][L]) for L in langs))
            class_manifest[table]["common_support"] = sorted(common)
            clean_L = [f"clean_{L}" for L in langs if f"clean_{L}" in pop]
            if len(clean_L) == len(langs):           # the clean subsets are compared among themselves
                class_manifest[table]["common_support_clean"] = sorted(
                    set.intersection(*(set(class_manifest[table][n]) for n in clean_L)))
    if len(langs) == 2:
        L1, L2 = langs      # used by the illustration below

    # ---- point estimates ------------------------------------------------------------------ #
    def table_block(name, ps):
        sub = counts_execution1(S, ps)
        mult = base_mult[name]
        block = {}
        for table in ("technology", "element", "evaluation_B", "comparison_B"):
            block[table] = {"micro_f1_weighted": micro(sub["per_art"], table, mult, True),
                            "micro_f1_unweighted": micro(sub["per_art"], table, mult, False)}
        for table in ("evaluation_A", "comparison_A"):
            block[table] = {"accuracy_weighted": accuracy_A(sub["A_units"], table, mult, True),
                            "accuracy_unweighted": accuracy_A(sub["A_units"], table, mult, False),
                            "units": sum(len(v) for v in sub["A_units"][table].values()),
                            "excluded": {k: v for k, v in sub["A_excluded"].items() if k.startswith(table)}}
        for table in ("element", "evaluation_A", "evaluation_B", "comparison_A", "comparison_B", "technology"):
            supported = sorted(class_manifest[table][name])
            full = sorted(set(INV[table]) | set(supported))
            pcw = per_class(sub["per_art"], table, mult, True, full)
            pcu = per_class(sub["per_art"], table, mult, False, full)
            mw, nw = macro_available({c: pcw[c] for c in supported})
            mu, nu = macro_available({c: pcu[c] for c in supported})
            block[table]["per_class"] = {
                c: {"f1_weighted": pcw[c], "f1_unweighted": pcu[c],
                    "support_rows": class_manifest[table][name].get(c, {}).get("rows", 0),
                    "support_articles": class_manifest[table][name].get(c, {}).get("articles", 0),
                    "in_macro": c in class_manifest[table][name],
                    "reported": class_manifest[table][name].get(c, {}).get("rows", 0) >= threshold}
                for c in full}
            block[table]["macro_f1_weighted"] = mw; block[table]["macro_f1_unweighted"] = mu
            block[table]["macro_classes"] = nw
            # L21: macro-F1 is not compared across languages when the supported class sets differ; the
            # common-support macro (classes supported in every language) is the sensitivity given instead
            if name in langs:
                common = class_manifest[table].get("common_support")
            elif name.startswith("clean_") and name != "clean_pooled":
                common = class_manifest[table].get("common_support_clean")
            else:
                common = None                          # pooled populations are not a language comparison
            if common is not None:
                cw, ncw = macro_available({c: pcw[c] for c in common})
                cu, _ = macro_available({c: pcu[c] for c in common})
                block[table]["macro_common_support_weighted"] = cw
                block[table]["macro_common_support_unweighted"] = cu
                block[table]["common_support_classes"] = ncw
        # exact match
        wc = sum(v[0] for v in sub["exact"].values()); wt = sum(v[1] for v in sub["exact"].values())
        c_ = sum(v[2] for v in sub["exact"].values()); t_ = sum(v[3] for v in sub["exact"].values())
        block["exact_match"] = {"weighted": None if wt == 0 else wc / wt, "unweighted": None if t_ == 0 else c_ / t_,
                                "rows": t_}
        return block, sub

    point = {}; subs = {}
    for name, ps in pop.items():
        point[name], subs[name] = table_block(name, ps)

    # ---- per-execution figures (W1 selection statistic; RQ3 reads stability instead) ------ #
    # The mean over executions of the execution-specific tuple micro-F1 is the pre-registered
    # W1 statistic (specification §4 W1); execution 1 stays the primary execution of every
    # reported table. `all_paragraphs_ok` is W1's eligibility read from the terminal outcomes.
    per_execution = {}
    for name, ps in pop.items():
        mult = base_mult[name]; per_execution[name] = {}
        for r in range(1, int(S["k"]) + 1):
            sub_r = counts_execution(S, ps, r)
            # the integer counts let a consumer recompute the F1 exactly (Codex W1 round 3, #10)
            ec = sub_r["C"]["element"]
            per_execution[name][str(r)] = {
                "element_counts_unweighted": {"tp": int(sum(v[3] for v in ec.values())),
                                              "fp": int(sum(v[4] for v in ec.values())),
                                              "fn": int(sum(v[5] for v in ec.values()))},
                "element_micro_f1_unweighted": micro(sub_r["per_art"], "element", mult, False),
                "element_micro_f1_weighted": micro(sub_r["per_art"], "element", mult, True),
                "technology_micro_f1_unweighted": micro(sub_r["per_art"], "technology", mult, False),
                "all_paragraphs_ok": all(S["outcome"].get(r, {}).get(q) in ("technologies_present", "off_topic")
                                         for q in ps)}
        vals = [v["element_micro_f1_unweighted"] for v in per_execution[name].values()]
        per_execution[name]["mean_element_micro_f1_unweighted"] = (sum(vals) / len(vals)
                                                                    if vals and all(v is not None for v in vals) else None)

    # ---- stability and displacement point estimates --------------------------------------- #
    stab = {p: stability_per_paragraph(S, p) for p in pids}
    pres = {p: presence(S, p) for p in pids}
    all_classes = sorted(set().union(*(g | q for g, q in pres.values())))

    def stab_block(ps):
        w = S["w"]
        out = {"paragraphs": len(ps), "paragraphs_without_pairs": sum(1 for p in ps if stab[p]["n_pairs"] == 0),
               "empty_union": sum(1 for p in ps if stab[p]["empty_union"]),
               "failure_rate": (sum(stab[p]["n_fail"] for p in ps) / (S["k"] * len(ps))) if ps else None}
        for key in ("tech", "elements", "evaluations", "comparison", "subtype", "any"):
            num = den = 0.0; nu = du = 0.0
            for p in ps:
                v = stab[p][key]
                if v is None:
                    continue
                num += w[p] * v; den += w[p]; nu += v; du += 1
            out[key] = {"weighted": None if den == 0 else num / den, "unweighted": None if du == 0 else nu / du,
                        "paragraphs": int(du)}
        return out

    def disp_block(ps):
        w = S["w"]; out = {}
        for c in all_classes:
            num = den = 0.0; nu = 0.0
            for p in ps:
                g, q = pres[p]
                num += w[p] * (int(c in q) - int(c in g)); den += w[p]; nu += (int(c in q) - int(c in g))
            out[c] = {"weighted_pp": None if den == 0 else 100 * num / den,
                      "unweighted_pp": None if not ps else 100 * nu / len(ps),
                      "gold_paragraphs": sum(c in pres[p][0] for p in ps),
                      "pred_paragraphs": sum(c in pres[p][1] for p in ps)}
        return out

    stability = {name: stab_block(ps) for name, ps in pop.items()}
    displacement = {name: disp_block(ps) for name, ps in pop.items()}

    # ---- resampling ----------------------------------------------------------------------- #
    rng = random.Random(seed)
    resample = {}
    absence = {}
    for name, ps in pop.items():
        arts = arts_of[name]
        sub = subs[name]
        draws = defaultdict(list); classes_defined = defaultdict(list); absent = defaultdict(int)
        art_paras = defaultdict(list)
        for p in ps:
            art_paras[S["art"][p]].append(p)
        for _ in range(B):
            mult = Counter(rng.choice(arts) for _ in arts)
            for table in ("technology", "element", "evaluation_B", "comparison_B"):
                draws[f"{table}.micro_f1_weighted"].append(micro(sub["per_art"], table, mult, True))
            for table in ("evaluation_A", "comparison_A"):
                draws[f"{table}.accuracy_weighted"].append(accuracy_A(sub["A_units"], table, mult, True))
            for table in ("technology", "element", "evaluation_A", "evaluation_B", "comparison_A", "comparison_B"):
                classes = sorted(class_manifest[table][name])
                pc = per_class(sub["per_art"], table, mult, True, classes)
                m, n = macro_available(pc, min_classes=1)      # L26: available-class macro inside a resample
                draws[f"{table}.available_class_macro_f1_weighted"].append(m)
                classes_defined[table].append(n)
                for c in classes:   # resample-absence: no article with gold support for c was drawn
                    if not any(mult.get(a_, 0) for a_ in sub["supp_arts"][table][c]):
                        absent[f"{table}:{c}"] += 1
            # stability and displacement: weighted means with multiplicity
            for key in ("tech", "elements", "evaluations", "comparison", "subtype", "any"):
                num = den = 0.0
                for a, m in mult.items():
                    for p in art_paras[a]:
                        v = stab[p][key]
                        if v is None:
                            continue
                        num += m * S["w"][p] * v; den += m * S["w"][p]
                draws[f"stability.{key}"].append(None if den == 0 else num / den)
            for c in all_classes:
                num = den = 0.0
                for a, m in mult.items():
                    for p in art_paras[a]:
                        g, q = pres[p]
                        num += m * S["w"][p] * (int(c in q) - int(c in g)); den += m * S["w"][p]
                draws[f"displacement.{c}"].append(None if den == 0 else 100 * num / den)
        resample[name] = {key: {"lo": pct([x for x in xs if x is not None], 0.025),
                                "hi": pct([x for x in xs if x is not None], 0.975),
                                "defined": sum(x is not None for x in xs), "B": B}
                          for key, xs in draws.items()}
        resample[name]["defined_class_counts"] = {t: dict(Counter(v)) for t, v in classes_defined.items()}
        absence[name] = {f"{table}:{c}": absent.get(f"{table}:{c}", 0) / B
                         for table in ("technology", "element", "evaluation_A", "evaluation_B", "comparison_A", "comparison_B")
                         for c in class_manifest[table][name]}     # measured for every supported class

    # ---- limits: macro-sign illustration on the actual supports ------------------------------- #
    illustration = {}
    if len(langs) == 2:
        for table, K in (("element", len(ELEMENTS)),):
            n1, n2 = len(class_manifest[table][L1]), len(class_manifest[table][L2])
            fval = 0.6
            illustration[table] = {"K": K, f"n_{L1}": n1, f"n_{L2}": n2, "f": fval,
                                   "gap_zero_convention": (n1 - n2) * (fval - 0.0) / K,
                                   "gap_one_convention": (n1 - n2) * (fval - 1.0) / K,
                                   "gap_positive_support_convention": 0.0,
                                   "note": "constructed illustration: an instrument scoring f on every "
                                           "supported class; the sign flips between conventions iff the "
                                           "supported-class counts differ"}

    # ---- attrition / abstention ---------------------------------------------------------- #
    attrition = {}
    for name, ps in pop.items():
        row = {"paragraphs": len(ps), "gold_off_topic": sum(1 for p in ps if p in S["gold_off"])}
        for r in range(1, S["k"] + 1):
            row[f"execution_{r}"] = dict(Counter(S["outcome"][r].get(p, "missing") for p in ps))
            # the registered joint count (completion plan, evaluator contract): technologies present on BOTH sides —
            # an overlapping subset of the terminal outcomes, kept as its own field, never summed with them
            row[f"execution_{r}_both_sides_technologies_present"] = sum(
                1 for p in ps if p not in S["gold_off"] and S["outcome"][r].get(p) == "technologies_present")
        attrition[name] = row

    # ---- clean-domain sensitivity: coverage of the clean populations (figures are in point/resample) -- #
    sensitivity = None
    if "clean_pooled" in pop:
        sensitivity = {name: {"paragraphs": len(ps), "articles": len(arts_of[name]),
                              "outlets": sorted({S["by_pid"][p].get("outlet", "") for p in ps}),
                              "classes_with_support": {t_: len(class_manifest[t_][name]) for t_ in ("element", "evaluation_B", "comparison_B")}}
                       for name, ps in pop.items() if name.startswith("clean_")}

    return {"run_id": inp["rec"]["run_id"], "dev": dev, "threshold": threshold, "k": S["k"], "B": B, "seed": seed,
            "model_key": inp["rec"].get("model_key"), "model_id": inp["rec"].get("model_id"),
            # agent review of §4k (2026-11-01): a comparison run's declaring section and the request it received, from
            # the verified execution contract; absent for every other run, so their metrics files are unchanged
            **({"model_comparison_authority": ((inp["rec"].get("execution_contract") or {}).get("model_comparison") or {}).get("authority"),
                "request_deviation": request_deviation(inp["rec"])}
               if (inp["rec"].get("execution_contract") or {}).get("model_comparison") else {}),
            # W3 (2026-10-14): instrument identity and analysis status derived from the VERIFIED execution contract
            # (Codex W3 round 3, #7/#8), never from a display block alone
            "instrument": instrument_identity(inp["rec"]),
            "analysis_status": analysis_status_of(inp["rec"], manifest_kind=(inp.get("manifest") or {}).get("kind")),
            "populations": {n: len(ps) for n, ps in pop.items()},
            "articles": {n: len(a) for n, a in arts_of.items()},
            "class_manifest": class_manifest, "point": point, "per_execution": per_execution, "resample": resample,
            "inputs": inp.get("provenance"),
            "stability": stability, "displacement": displacement, "resample_absence": absence,
            "illustration": illustration, "attrition": attrition, "clean_domain_sensitivity": sensitivity,
            "conventions": {
                "universe": "outer join over every manifest paragraph; gold off-topic = empty gold set",
                "weights": "design weights 1/pi per paragraph on every count; unweighted beside it",
                "macro": "point estimate over classes with positive gold support in the population; "
                         "resampled statistic = available-class macro-F1",
                "reportability": f"per-class cell printed when support_rows >= {threshold}; suppression "
                                 f"does not change aggregates or macro membership",
                "undefined": "TP+FP+FN = 0 or an empty denominator stays undefined",
                "stability": "conditional on successful executions; empty technology union has no relation-level score",
                "displacement": "design-weighted, reference-relative, for the eligible frame; no bias-correction estimator; "
                                "failed execution 1 counts as absence"}}


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #
def write_tables(res: dict, out_dir: Path, class_manifest_path: Path) -> None:
    # every refusal check BEFORE any artefact is replaced
    cm_text = json.dumps(res["class_manifest"], indent=2, ensure_ascii=False) + "\n"
    if class_manifest_path.exists() and class_manifest_path.read_text(encoding="utf-8") != cm_text:
        raise EvalError(f"{class_manifest_path.name} exists with a different class population; the frozen "
                        f"class manifest of a reporting run is written once. Use a new run id or remove it deliberately.")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    class_manifest_path.write_text(cm_text, encoding="utf-8")
    status = res.get("analysis_status") or ("development" if res["dev"] else "registered primary")
    inst_name = (res.get("instrument") or {}).get("name", "v1.0")
    if status == "development":
        stamp = "DEVELOPMENT -- not reportable" + (f" (instrument {inst_name}, W3 post-registration development)" if inst_name != "v1.0" else "")
    elif status == "exploratory reused A2":
        stamp = f"EXPLORATORY -- reused A2, not the registered result (instrument {inst_name}, W3)"
    elif status == sc.A3_STATUS:
        stamp = f"FRESH-SAMPLE EVALUATION -- A3 census ({inst_name}), not the registered result; intervals indicative"
    elif status == sc.MODEL_COMPARISON_STATUS:
        stamp = comparison_stamp(res)
    elif status == sc.FORM_STATUS:
        stamp = (f"POST-DRAFT INSTRUMENT VARIANT -- {inst_name} (specification §4j), inspected sample, descriptive, "
                 f"not the registered result")
    elif status == sc.CONTEXT_STATUS:
        stamp = (f"POST-REVIEW CONTEXT VARIANT -- {inst_name} (specification §4l (5)): the registered request with the "
                 f"headline, outlet and language before the paragraph; inspected sample, descriptive, not the registered result")
    elif status == sc.TRANSLATED_STATUS:
        stamp = ("EXPLORATORY -- translated inputs (specification §4e(a)): the German A2 paragraphs machine-translated into "
                 "English, scored against the unchanged German reference; a comparison of complete configurations, not a "
                 "language effect, not the registered result")
    else:
        stamp = "reportable"
    s = res["threshold"]
    on_a3 = status == sc.A3_STATUS or (res.get("inputs") or {}).get("sample_kind") == sc.A3_KIND
    thr_word = "declared" if on_a3 else "sealed"      # §4b: the A3 threshold is declared, not sealed

    def cell(v, reported=True, support=None):
        # L27, three states: undefined (zero support and zero predictions) is not a suppressed cell
        if v is None:
            return "undefined"
        if not reported:
            return f"n<{s} ({support})"
        return fmt(v)

    def primary_rows(names):
        rows = [["population", "dimension", "score", "weighted", "unweighted", "interval (2.5-97.5, weighted)"]]
        for name in names:
            block = res["point"][name]; rs = res["resample"][name]
            for table, label in (("element", "elements (tuples)"), ("technology", "technology detection"),
                                 ("evaluation_B", "evaluation, score B"), ("comparison_B", "comparison target, score B")):
                iv = rs.get(f"{table}.micro_f1_weighted", {})
                rows.append([name, label, "micro-F1", fmt(block[table]["micro_f1_weighted"]),
                             fmt(block[table]["micro_f1_unweighted"]), f"[{fmt(iv.get('lo'))}, {fmt(iv.get('hi'))}]"])
            for table, label in (("evaluation_A", "evaluation, score A"), ("comparison_A", "comparison target, score A")):
                iv = rs.get(f"{table}.accuracy_weighted", {})
                rows.append([name, label, f"accuracy on {block[table]['units']} matched units",
                             fmt(block[table]["accuracy_weighted"]), fmt(block[table]["accuracy_unweighted"]),
                             f"[{fmt(iv.get('lo'))}, {fmt(iv.get('hi'))}]"])
            for table in ("element", "evaluation_A", "evaluation_B", "comparison_A", "comparison_B"):
                iv = rs.get(f"{table}.available_class_macro_f1_weighted", {})
                dist = res["resample"][name]["defined_class_counts"].get(table, {})
                rows.append([name, table, f"macro-F1 over {block[table]['macro_classes']} supported classes "
                             f"(resampled: available-class; defined-class counts {dict(sorted(dist.items(), key=lambda kv: -int(kv[0])))})",
                             fmt(block[table]["macro_f1_weighted"]), fmt(block[table]["macro_f1_unweighted"]),
                             f"[{fmt(iv.get('lo'))}, {fmt(iv.get('hi'))}]"])
            for table in ("element", "evaluation_A", "evaluation_B", "comparison_A", "comparison_B"):
                if "macro_common_support_weighted" in block[table]:
                    rows.append([name, table, f"macro-F1 over {block[table]['common_support_classes']} common-support classes "
                                 f"(sensitivity, point estimate; L21)",
                                 fmt(block[table]["macro_common_support_weighted"]), fmt(block[table]["macro_common_support_unweighted"]), ""])
            rows.append([name, "row exact match", f"{block['exact_match']['rows']} rows", fmt(block["exact_match"]["weighted"]),
                         fmt(block["exact_match"]["unweighted"]), ""])
        return rows
    main_pops = [n for n in res["point"] if not n.startswith("clean_")]
    clean_pops = [n for n in res["point"] if n.startswith("clean_")]
    _write(out_dir / "table1_primary", primary_rows(main_pops), f"Primary metric table, run {res['run_id']} ({stamp}). {'Weighted = weight one (the A3 census)' if on_a3 else 'Weighted = design weights'}; interval = percentile over {res['B']} article resamples; the defined-class-count distribution over resamples is printed with every available-class macro.")
    if False:
        rs = None
    # (table 1 written above)
    # Table 2: per class with support and reportability
    rows = [["population", "dimension", "class", "F1 weighted", "F1 unweighted", "support rows", "support articles", "resample absence"]]
    for name, block in res["point"].items():
        for table in ("element", "technology", "evaluation_A", "evaluation_B", "comparison_A", "comparison_B"):
            for c, v in block[table]["per_class"].items():
                ab = res["resample_absence"][name].get(f"{table}:{c}")
                one = "*" if (v["reported"] and v["f1_weighted"] is not None and v["support_articles"] == 1) else ""
                rows.append([name, table, c, cell(v["f1_weighted"], v["reported"], v["support_rows"]) + one,
                             cell(v["f1_unweighted"], v["reported"], v["support_rows"]) + one,
                             v["support_rows"], v["support_articles"],
                             "not measured (no support)" if ab is None else fmt(ab)])
    _write(out_dir / "table2_per_class", rows, f"Per-class F1 with support in rows and articles and the resample-absence rate; "
           f"no per-class interval is computed. Cells: value, or n<s below the {thr_word} threshold s={s}, or undefined when "
           f"support and predictions are both zero; * marks a printed cell whose support comes from a single article ({stamp}).")
    # Table 4: stability
    rows = [["population", "dimension", "change rate weighted", "unweighted", "interval", "paragraphs", "without pairs", "empty union", "failure rate"]]
    for name, st in res["stability"].items():
        for key in ("tech", "elements", "evaluations", "comparison", "subtype", "any"):
            iv = res["resample"][name].get(f"stability.{key}", {})
            rows.append([name, key, fmt(st[key]["weighted"]), fmt(st[key]["unweighted"]),
                         f"[{fmt(iv.get('lo'))}, {fmt(iv.get('hi'))}]", st[key]["paragraphs"],
                         st["paragraphs_without_pairs"], st["empty_union"], fmt(st["failure_rate"])])
    _write(out_dir / "table4_stability", rows, f"Identical-execution stability, k={res['k']}, conditional on successful executions ({stamp}).")
    # Table 5: displacement
    rows = [["population", "class", "displacement pp weighted", "unweighted", "interval", "gold paragraphs", "predicted paragraphs"]]
    for name, d in res["displacement"].items():
        for c, v in d.items():
            iv = res["resample"][name].get(f"displacement.{c}", {})
            ok = v["gold_paragraphs"] >= s
            rows.append([name, c, cell(v["weighted_pp"], ok, v["gold_paragraphs"]) if ok else f"n<{s} ({v['gold_paragraphs']})",
                         fmt(v["unweighted_pp"], 1) if ok else f"n<{s} ({v['gold_paragraphs']})",
                         f"[{fmt(iv.get('lo'), 1)}, {fmt(iv.get('hi'), 1)}]" if ok else "",
                         v["gold_paragraphs"], v["pred_paragraphs"]])
    _write(out_dir / "table5_displacement", rows, f"{'Unweighted (census, weight one)' if on_a3 else 'Design-weighted'}, reference-relative prevalence displacement for {'the A3 census' if on_a3 else 'the eligible frame'}, percentage points, no bias-correction estimator; a class whose reference-positive paragraphs number fewer than the {thr_word} threshold s={s} prints n<s with that count ({stamp}). Opposing errors can cancel; the interval carries no uncertainty from the human reference.")
    # Table 3: categorical layers -- score A coverage beside score B
    rows = [["population", "dimension", "score A accuracy (weighted)", "matched units", "excluded units (reason: n)",
             "score B micro-F1 (weighted)", "score A macro (supported classes)", "score B macro (supported classes)"]]
    for name, block in res["point"].items():
        for a_, b_, label in (("evaluation_A", "evaluation_B", "evaluation"), ("comparison_A", "comparison_B", "comparison target")):
            A, Bt = block[a_], block[b_]
            rows.append([name, label, fmt(A["accuracy_weighted"]), A["units"],
                         "; ".join(f"{k_.split(':')[1]}: {v}" for k_, v in A["excluded"].items()) or "none",
                         fmt(Bt["micro_f1_weighted"]), f"{fmt(A['macro_f1_weighted'])} over {A['macro_classes']}",
                         f"{fmt(Bt['macro_f1_weighted'])} over {Bt['macro_classes']}"])
    _write(out_dir / "table3_categorical", rows, f"Categorical layers: score A conditional on the parent being detected (post-hoc intersection), score B end-to-end labelled tuples ({stamp}).")
    # Table 6: limits
    rows = [["population", "table", "supported classes", "of inventory", "common-support classes", "articles", "macro-sign illustration"]]
    ill = res["illustration"].get("element", {})
    ill_txt = (f"K={ill['K']}, n_de={ill.get('n_de')}, n_en={ill.get('n_en')}, f={ill['f']}: gap {fmt(ill['gap_zero_convention'])} (zero) / "
               f"{fmt(ill['gap_one_convention'])} (one) / 0.000 (positive-support)") if ill else "n/a (one language)"
    rows[0].append("defined-class counts over resamples (count: resamples)")
    for name in main_pops:
        for table in ("technology", "element", "evaluation_B", "comparison_B"):
            cm = res["class_manifest"][table]
            dist = res["resample"][name]["defined_class_counts"].get(table, {})
            rows.append([name, table, len(cm[name]), len(INV[table]), len(cm.get("common_support", [])) if name == "pooled" else "",
                         res["articles"][name], ill_txt if (name == "pooled" and table == "element") else "",
                         "; ".join(f"{k_}: {v}" for k_, v in sorted(dist.items(), key=lambda kv: -int(kv[0])))])
    _write(out_dir / "table6_limits", rows, f"Limits of validation: supported classes per language, cluster counts, and the constructed macro-sign illustration ({stamp}).")
    # Table 7: clean-domain sensitivity
    cs = res.get("clean_domain_sensitivity")
    if cs:
        rows = primary_rows(clean_pops)
        cov = [["population", "paragraphs", "articles", "outlets", "classes with support"]] + [
            [n, v["paragraphs"], v["articles"], ", ".join(v["outlets"]), json.dumps(v["classes_with_support"])] for n, v in cs.items()]
        _write(out_dir / "table7_clean_domain", rows, f"Clean-domain sensitivity: the full primary table recomputed on paragraphs from articles with no calibration exposure ({stamp}); coverage in table7_clean_domain_coverage.")
        _write(out_dir / "table7_clean_domain_coverage", cov, f"Coverage of the clean-domain populations ({stamp}).")
    else:
        _write(out_dir / "table7_clean_domain", [["restriction", "note"], ["not applicable", (
            "A3 census: every paragraph is from an article with no documented exposure; table 7 coincides with table 1 and is not produced (specification §4b)"
            if status == sc.A3_STATUS else "no exposure classes in this manifest, or nothing to exclude")]],
               f"Clean-domain sensitivity ({stamp}).")
    # Table 9: human reliability, if supplied
    if res.get("reliability"):
        rl = res["reliability"]
        rows = [["level", "item", "alpha", "raw agreement", "n", "support (first coding)", "support (second coding)", "support articles", "coverage"]]
        def rcell(v):
            # the same three states as every other per-class cell: undefined / n<s / value (L27)
            return cell(v["alpha"], v["support_rows"] >= s, v["support_rows"]), cell(v["raw"], v["support_rows"] >= s, v["support_rows"])
        for t_, v in rl["technology"].items():
            al_, raw_ = rcell(v)
            rows.append(["technology tick", t_, al_, raw_, v["n"], v["support_rows"], v.get("support_rows_b", ""), v["support_articles"], ""])
        for e_, v in rl["elements"].items():
            al_, raw_ = rcell(v)
            rows.append(["element (aligned rows)", e_, al_, raw_, v["n"], v["support_rows"], v.get("support_rows_b", ""), v["support_articles"],
                         f"rows only one coder: {rl['rows_only_a']}+{rl['rows_only_b']}"])
        for f_, v in rl["evaluation"].items():
            al_, raw_ = rcell(v)
            rows.append(["evaluation (both fired)", f_, al_, raw_, v["n"], v["support_rows"], "", v["support_articles"], v["mask_coverage"]])
        v = rl["comparison_target"]; al_, raw_ = rcell(v)
        rows.append(["comparison target (aligned rows)", "", al_, raw_, v["n"], v["support_rows"], "", v["support_articles"], ""])
        _write(out_dir / "table9_reliability", rows, f"Human reliability, {rl.get('route', '')}, on aligned units; items whose first-coding "
               f"support is below the {thr_word} threshold s={s} print n<s, items without variation print undefined ({stamp}).")
    # Table 8: attrition
    rows = [["population", "paragraphs", "gold off-topic", "execution", "outcomes (partition)",
             "technologies present on both sides (subset)"]]
    for name, at in res["attrition"].items():
        for r in range(1, res["k"] + 1):
            rows.append([name, at["paragraphs"], at["gold_off_topic"], r, json.dumps(at[f"execution_{r}"]),
                         at.get(f"execution_{r}_both_sides_technologies_present", "")])
    _write(out_dir / "table8_attrition", rows, f"Attrition and abstention per execution; the last column is the "
           f"registered joint count, an overlapping subset of the outcomes, not a further outcome ({stamp}).")


TEXT_WIDTH_PT = 455.0   # the thesis text width (160 mm); the writer never lets a table exceed it
CHAR_PT = 5.0           # approximate width of one character in \footnotesize
BOLD = 1.15             # bold headings are wider
COLSEP_PT = 6.0         # \tabcolsep on each side of a column


def _write(base: Path, rows: list[list], caption: str) -> None:
    """CSV plus a LaTeX rendering. Every table is a longtable (paginates, so no float is ever too large for a
    page); columns are bounded wrapping columns whose minimum is the longest unbreakable token; when the minima of
    all columns do not fit the text width the table is split into column groups ("part i of n"), each repeating
    the first two identity columns. The CSV holds every cell once; the TeX parts together hold every column."""
    with base.with_suffix(".csv").open("w", newline="", encoding="utf-8") as f:
        csv.writer(f, delimiter=";").writerows(rows)
    head, body = rows[0], rows[1:]
    import re as _re

    def esc(x):
        s = str(x).replace("_", "\\_\\allowbreak{}").replace("%", "\\%").replace("<", "$<$")
        for ch in (":", "=", "|"):
            s = s.replace(ch, ch + "\\allowbreak{}")
        return s

    def tokens(s): return [t for t in _re.split(r"[ _:=|]+", str(s)) if t]
    n = len(head)
    min_pt = []
    for i in range(n):
        body_tok = max((max(len(t) for t in tokens(r[i])) for r in body if tokens(r[i])), default=1) * CHAR_PT
        head_tok = max((len(t) for t in tokens(head[i])), default=1) * CHAR_PT * BOLD
        min_pt.append(max(body_tok, head_tok) + 2)
    content = [max(1, min(max(len(str(r[i])) for r in rows), 80)) for i in range(n)]
    # identity columns repeated in every part: all leading key columns of the table (population, dimension, class, ...)
    IDENTITY = {"population", "dimension", "class", "table", "level", "item", "execution", "score"}
    n_id = 0
    while n_id < n and str(head[n_id]).split(" ")[0] in IDENTITY:
        n_id += 1
    n_id = max(1, min(n_id, n))
    # column groups: greedily add columns while the minima (plus tabcolsep) fit the text width
    groups, cur = [], list(range(n_id))
    for i in range(n_id, n):
        trial = cur + [i]
        if sum(min_pt[c] for c in trial) + 2 * COLSEP_PT * len(trial) <= TEXT_WIDTH_PT or len(cur) == n_id:
            cur = trial
        else:
            groups.append(cur); cur = list(range(n_id)) + [i]
    groups.append(cur)
    parts = []
    for g_i, cols in enumerate(groups, 1):
        avail = TEXT_WIDTH_PT - 2 * COLSEP_PT * len(cols)
        mins = [min_pt[c] for c in cols]
        if sum(mins) > avail:                                 # a single column wider than the page: scale, nothing else helps
            mins = [m * avail / sum(mins) for m in mins]
        rest = max(0.0, avail - sum(mins)); tot = sum(content[c] for c in cols)
        widths = [m + rest * content[c] / tot for m, c in zip(mins, cols)]
        colspec = "".join(f">{{\\raggedright\\arraybackslash}}p{{{w:.1f}pt}}" for w in widths)
        cap = esc(caption) + (f" (part {g_i} of {len(groups)})" if len(groups) > 1 else "")
        header = " & ".join(f"\\textbf{{{esc(head[c])}}}" for c in cols) + " \\\\"
        lines = ["\\begingroup\\footnotesize", "\\begin{longtable}{" + colspec + "}",
                 f"\\caption{{{cap}}} \\\\", "\\toprule", header, "\\midrule", "\\endfirsthead",
                 "\\toprule", header, "\\midrule", "\\endhead", "\\bottomrule", "\\endfoot"]
        lines += [" & ".join(esc(r[c]) for c in cols) + " \\\\" for r in body]
        lines += ["\\end{longtable}", "\\endgroup"]
        parts.append("\n".join(lines))
    base.with_suffix(".tex").write_text("\n\n".join(parts) + "\n", encoding="utf-8")


def request_deviation(rec: dict) -> dict:
    """How a comparison run's request differs from the pinned one, read from its execution contract (§4f cache point,
    §4g and §4k decoding); an empty dict when it does not."""
    c = rec.get("execution_contract") or {}
    inf = c.get("inference_config") or {}
    out = {}
    if inf.get("temperature", 0) != 0 or "temperature" not in inf:
        out["temperature"] = inf.get("temperature", "not sent")
    if c.get("additional_model_request_fields"):
        out["additional_model_request_fields"] = c["additional_model_request_fields"]
    if c.get("cache_point") is False:
        out["cache_point"] = False
    if inf.get("maxTokens") not in (None, 1024):
        out["max_tokens"] = inf["maxTokens"]
    if c.get("tool_choice"):                                       # §4m: only where the adaptation changes it
        out["tool_choice"] = c["tool_choice"]
    return out


def comparison_stamp(res: dict) -> str:
    """The table stamp of a robustness-model-comparison run: its declaring section (§4f, §4g or §4k, from the contract)
    and its request deviation in words (agent review of §4k, 2026-11-01: the stamp named §4f for every such run)."""
    auth = res.get("model_comparison_authority") or sc.MODEL_COMPARISON_AUTHORITY
    sec = auth.split("§", 1)[1].split()[0] if "§" in auth else "4f"
    d = res.get("request_deviation") or {}
    words = []
    if "temperature" in d:
        words.append("no temperature" if d["temperature"] == "not sent" else f"temperature {d['temperature']}")
    extra = d.get("additional_model_request_fields") or {}
    if extra.get("thinking") == {"type": "disabled"}:
        words.append("thinking disabled")
    if "reasoning_effort" in extra:
        words.append(f"reasoning_effort {extra['reasoning_effort']}")
    if extra.get("thinking") == {"type": "adaptive"}:                # §4m
        words.append(f"adaptive thinking, effort {(extra.get('output_config') or {}).get('effort')}")
    if d.get("tool_choice") == {"auto": {}}:
        words.append("tool choice auto")
    if d.get("cache_point") is False:
        words.append("no cache point")
    if "max_tokens" in d:
        words.append(f"{d['max_tokens']} tokens")
    req = f" ({', '.join(words)})" if words else ""
    k = sc.model_comparison_repeats(res.get("model_key"))
    return (f"ROBUSTNESS MODEL COMPARISON -- v1.0 under {res.get('model_key') or 'another model'}{req}, "
            f"{'one execution' if k == 1 else f'{k} executions'}, not the registered result (specification §{sec})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-record", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--restrict-split", default=None)
    ap.add_argument("--gold-pass1", required=True)
    ap.add_argument("--gold-pass2", required=True)
    ap.add_argument("--threshold", type=int, required=True, help="the author's blind minimum support s")
    ap.add_argument("--resamples", type=int, default=DEFAULT_B)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--dev", action="store_true")
    ap.add_argument("--reliability", nargs=4, metavar=("P1_A", "P2_A", "P1_B", "P2_B"), default=None)
    ap.add_argument("--reliability-route", choices=["A", "B"], default=None)
    ap.add_argument("--washout-days", type=int, default=WASHOUT_DAYS_REGISTERED,
                    help=f"route B: minimum days between the reference's last coding and the re-code's first (registered {WASHOUT_DAYS_REGISTERED}; any other value is a disclosed deviation and needs --washout-deviation)")
    ap.add_argument("--washout-deviation", default=None, help="route B: who decided the shortened washout, when and why; recorded in the metrics")
    ap.add_argument("--gold-manifest", default=None, help="the reference session's manifest (default: inferred)")
    ap.add_argument("--skip-provenance", action="store_true",
                    help="fixture tests only: skip the sessions.csv and session-manifest binding of the reference")
    a = ap.parse_args()
    try:
        inp = load_inputs(a)
    except EvalError as exc:
        sys.exit(f"REFUSING to evaluate: {exc}")
    res = evaluate(inp, threshold=a.threshold, B=a.resamples, seed=a.seed, dev=a.dev)
    if a.reliability:
        p1a, p2a, p1b, p2b = (read_csv(Path(x)) for x in a.reliability)
        designated = {p["paragraph_id"] for p in inp["paras"] if p.get("reliability_subsample")} or None
        try:
            if a.reliability_route not in ("A", "B"):
                raise EvalError("--reliability-route A or B is required with --reliability")
            prov = bind_reliability_inputs(a, inp, list(a.reliability), [p1a, p2a, p1b, p2b],
                                           washout_days=a.washout_days, deviation=a.washout_deviation)
            rel = reliability(p1a, p2a, p1b, p2b, inp["frame_of"], designated)
        except EvalError as exc:
            sys.exit(f"REFUSING to compute reliability: {exc}")
        _gap = prov.get("washout_days")
        rel["route"] = {"A": "inter-coder reliability",
                        "B": (f"intra-coder temporal stability ({_gap}-day interval; applied minimum {a.washout_days}, "
                              f"registered {WASHOUT_DAYS_REGISTERED}; deviation disclosed)"
                              if a.washout_days != WASHOUT_DAYS_REGISTERED else
                              f"intra-coder temporal stability ({_gap}-day interval, {WASHOUT_DAYS_REGISTERED}-day washout)")}[a.reliability_route]
        rel["inputs"] = prov
        res["reliability"] = rel
    out_dir = RUNS / "tables" / ("dev" if a.dev else "") / res["run_id"]
    cm_path = (out_dir if a.dev else RUNS) / f"{res['run_id']}_class_manifest.json"
    try:
        write_tables(res, out_dir, cm_path)
    except EvalError as exc:
        sys.exit(f"REFUSING to write: {exc}")
    pooled = res["point"]["pooled"]
    print(f"evaluated run {res['run_id']} ({'DEVELOPMENT' if a.dev else res.get('analysis_status', 'reportable')}): "
          f"{res['populations']} paragraphs, {res['articles']} articles")
    print(f"  headline element micro-F1 weighted {fmt(pooled['element']['micro_f1_weighted'])} "
          f"(unweighted {fmt(pooled['element']['micro_f1_unweighted'])}), technology {fmt(pooled['technology']['micro_f1_weighted'])}")
    print(f"  tables in {out_dir.relative_to(ROOT)}; class manifest {cm_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
