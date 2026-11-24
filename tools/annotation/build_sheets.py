#!/usr/bin/env python3
"""Build the annotation sheets from the pilot corpus, and route paragraphs to splits.

Part of module M5 (see ../PROTOTYPE-PLAN.md), written ahead of the extraction
pipeline because annotation is on the critical path and needs no model. Run
`--report` for the current counts; do not quote figures out of this docstring.

What it does
------------
1. Extracts *genuine article prose*. This is the part that is easy to get wrong,
   because most of each file is the collector's own notes -- vocabulary lists for
   the keyword design, speaker inventories, "why this article is in the set" --
   which is NOT article text and must never reach an annotation sheet. Only a
   file whose frontmatter declares `text_status: full` or `PARTIAL` contributes
   prose; EXTRACT ONLY and METADATA ONLY contribute none. Segmenting naively
   produces far more "paragraphs" than segmenting correctly does.
2. Routes every paragraph to exactly one split by LOOKUP, never by recomputation
   -- see `split_registry.py`, which owns that decision and every invariant
   protecting it.
3. Writes working sheets into sheets/, which is gitignored because they carry
   licensed article text. It writes NO sheet for a closed split: those sheets are
   the provenance of finalized label files.

How routing works, and what it used to get wrong
------------------------------------------------
The evaluation set must stay disjoint from anything the prompt or codebook was
tuned against, and "I will remember not to reuse those" is not a mechanism.

The mechanism used to be an article-level map plus size targets, and it had two
defects that between them coded three paragraphs twice and would have put nine
already-coded paragraphs into the gold standard. It counted only NEWLY assigned
articles against its size targets, so a rerun quietly refilled both development
sets; and it read only the article-level map, so paragraph-level marks were
invisible to it.

What replaced it: corpus membership is a list of immutable versions whose unit is
the identity triple `(article_id, paragraph_id, sha256)`, and assignment is an
append-only, checksum-chained log. A0, A0b, A1 and A1b are terminal. An
unrecognised paragraph inside a closed-split article aborts the build; one that
contains a recorded paragraph's text is quarantined rather than admitted. The
design argument is in ../CORPUS-INTEGRATION-PLAN.md and the twelve review rounds
that produced it are in ../CORPUS-INTEGRATION-REVIEW-LOG.md.

What it deliberately does NOT do
--------------------------------
It does not pre-fill the technology columns from the keyword prefilter, even
though it knows the answer. A visible hint would anchor the annotator and
contaminate the very labels the model is judged against. Binary columns are
pre-filled with 0 only so that the annotator types 1s and nothing else.

Usage
-----
    python tools/annotation/build_sheets.py            # build/refresh the sheets
    python tools/annotation/build_sheets.py --report   # counts only, write nothing
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
import tarfile
import platform
import subprocess
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

# Seed set at the top of the script, per the chair's thesis guide. Governs the
# A0/A1 draw only; there is no model call in this file.
SEED = 20260829
random.seed(SEED)

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "corpus"
OUT = Path(__file__).resolve().parent / "sheets"
SPLIT_FILE = Path(__file__).resolve().parent / "split-assignment.json"

CODEBOOK_VERSION = "1.0"
ANNOTATOR = "NW"
MIN_WORDS = 15

# German-locale Excel expects ";" and a BOM. One format everywhere; readers sniff.
DELIM = ";"
ENC = "utf-8-sig"

# Sized against 125 candidate paragraphs, not against the 650 the exposé assumed.
# A0 is discarded after use; A1 is iterated against freely; everything else is
# reserved so that a pilot-only evaluation set remains possible.
A0_SIZE = 15
A1_SIZE = 35

FRONTMATTER = re.compile(r"<!--(.*?)-->", re.S)

# Every invariant that protects the gold standard lives in split_registry, not
# here. This module decides what a paragraph IS; the registry decides where it may
# GO, and refuses rather than guessing. See tools/CORPUS-INTEGRATION-PLAN.md.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import split_registry as sr                                          # noqa: E402

# A paragraph carrying a transcription gap is FLAGGED, never dropped: the count
# belongs in the attrition table, and dropping it silently would make the reported
# attrition understate what the corpus cost. Plan step P8. Enforcement in the
# sheet/finalizer schemas needs the versioned-schema refactor and is out of scope,
# so the flag lands on collected rows and ALL_paragraphs.csv only.
GAP_MARKER = "[unclear:"


# Keyword prefilter. Used ONLY to decide which paragraphs are candidates and to
# stratify the draw. Never written into a sheet the annotator sees.
TECH_PATTERNS = {
    "general":  r"renewable|erneuerbar|clean energy|energiewende|green energy|ökostrom|grünstrom",
    "solar":    r"solar|photovolta|pv-anlage|solaranlag|sonnenenergie|sonnenstrom",
    "wind":     r"\bwind|windkraft|windrad|windrä|windpark|offshore|onshore",
    "hydrogen": r"hydrogen|wasserstoff|elektrolyse|electroly",
    "nuclear":  r"nuclear|atomkraft|kernkraft|akw\b|reaktor|reactor|kernenerg",
    "other":    r"hydropower|wasserkraft|geotherm|biomass|biomasse|biogas|tidal|"
                r"gezeiten|pumpspeicher|battery stor|batteriespeicher|speicher\b|storage\b",
}

SHEET1_COLUMNS = [
    # context: read, do not edit
    "paragraph_id", "article_id", "lang", "outlet", "country", "pub_date",
    "headline", "paragraph_text",
    # STEP 1 — which technologies does this paragraph make a statement about? (1 = yes)
    "tech_general", "tech_solar", "tech_wind", "tech_hydrogen",
    "tech_nuclear", "tech_other_renewable", "tech_storage",
    "wind_siting", "hydrogen_pathway",
    # STEP 2 — does the paragraph perform any framing function at all?
    "no_frame_present",
    # notes and provenance
    "confidence", "note",
    "annotator", "annotator_type", "coded_at", "codebook_version", "sitting",
    "text_sha256",
]

# One row per (paragraph x technology). The frame elements live HERE, not on the
# paragraph row: in a multi-technology paragraph, "F1a fired" is meaningless
# unless it says which technology it fired for. Evaluation then attaches to the
# frame, which is why there is one evaluation column per frame rather than one
# per row.
SHEET2_COLUMNS = [
    "row_id", "paragraph_id", "technology",
    # subtype of THIS technology, carried from pass 1 (blank for the other five)
    "wind_siting", "hydrogen_pathway",
    # context carried across so nothing has to be looked up
    "lang", "outlet", "headline", "paragraph_text",
    # STEP 3 — the ten+ frame elements, FOR THIS TECHNOLOGY (1 = fires)
    "F1a", "F1b", "F2a", "F2b", "F2c", "F3a", "F3b", "F4a", "F4b", "F5a", "F5b",
    "F6a", "F6b", "F7a", "F7b", "F8a", "F9",
    "no_frame_for_this_tech",
    # STEP 5 — evaluation per fired frame. Leave blank where the frame did not fire.
    "eval_costs", "eval_jobs", "eval_climate", "eval_security", "eval_reliability",
    "eval_feasibility", "eval_siting", "eval_regulation", "eval_other",
    # STEPS 6, 7, 8
    "comparison_target", "comparison_note",
    "speaker", "confidence", "note",
    "annotator", "annotator_type", "coded_at", "codebook_version", "sitting",
    # Identity of the text this row was coded against. Pass 1 has carried it since
    # v1.0; pass 2 did not, so a pass-2 label file could not establish WHICH text a
    # row was coded on (CORPUS-INTEGRATION-PLAN.md "out of scope" item 1). Propagated
    # by expand_sheet2.py from the pass-1 row; verified by finalize.py against the
    # session manifest by RECOMPUTING it from the sheet text.
    "text_sha256",
]

# Second header row: what each column actually asks for, in words. Written as a
# data row whose row_id is "#labels" so every reader can skip it unambiguously.
# English per the repository's language rule, even though the work is German.
SHEET2_LABELS = {
    "row_id": "#labels",
    "paragraph_id": "(do not edit)",
    "technology": "(do not edit)",
    "lang": "(context)",
    "outlet": "(context)",
    "headline": "(context)",
    "paragraph_text": "READ THIS — code the row for the technology named in column C",
    "F1a": "cost / price / bills / cheaper-dearer",
    "F1b": "subsidy / levy / auction / public funding / who pays",
    "F2a": "jobs created, lost, secured / workers / closures",
    "F2b": "investment / industry / factory or project decisions / exports / competitiveness",
    "F2c": "deployment GROWTH, a RECORD, or a DECLINE — a figure with a direction",
    "F3a": "emissions / carbon / climate targets",
    "F3b": "transition or phase-out pathway (no emissions claim)",
    "F4a": "import dependence or independence / foreign supplier",
    "F4b": "supply security / sovereignty / geopolitics (no mechanism needed)",
    "F5a": "availability when needed — intermittency, weather, backup, baseload",
    "F5b": "grid / connection queue / transmission lines / curtailment",
    "F6a": "can it be built, rebuilt, restarted, run AT ALL — technical obstacles",
    "F6b": "efficiency / yield / output potential of a design or site",
    "F7a": "land or area requirement / competing land use",
    "F7b": "landscape / visual / noise / local acceptance / siting conflict",
    "F8a": "permitting / approval procedure / legal classification / planning law",
    "F9": "other frame — NOTE REQUIRED",
    "no_frame_for_this_tech": "1 = this technology is named but not framed here",
    # eval_* hints are GENERATED below, from the codebook's own evaluation values and
    # frame derivation. They were hand-written and had drifted twice: they told the
    # annotator to write `pos|neg`, which the validator refuses, and `eval_jobs` still
    # said "only if F2a or F2b fired" after v1.0 added F2c to that frame. A sheet that
    # instructs invalid values is worse than one that says nothing.
    "comparison_target": ("natural_gas|coal|oil|fossil_generic|grid_status_quo|"
                          "general|solar|wind|hydrogen|nuclear|other_renewable|storage|"
                          "multiple|none|other"),
    "wind_siting": "onshore|offshore|unspecified — wind rows only",
    "hydrogen_pathway": "green|blue|gray|unspecified — hydrogen rows only",
    "comparison_note": "free text — required if comparison_target = other",
    "speaker": "outlet|political|industry|ngo|expert|unattributed|ambiguous",
    "confidence": "1 = guessing  2 = fairly sure  3 = clear",
    "note": "what did the codebook not tell you?",
    "annotator": "(prefilled)", "coded_at": "date", "codebook_version": "(prefilled)",
    "annotator_type": "human | model — MUST be `human` for any reported session",
    "sitting": "1, 2, 3 …",
    "text_sha256": "(do not edit)",
}


def _add_generated_eval_labels() -> None:
    """Fill the eval_* label hints from the frozen codebook. Single source, so
    these cannot drift from what finalize.py will accept."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    import codebook_reader
    cb = codebook_reader.load()
    values = "|".join(cb.evaluations)
    for frame, elements in cb.frame_derivation.items():
        cond = " or ".join(elements) if len(elements) > 1 else elements[0]
        SHEET2_LABELS[f"eval_{frame}"] = f"{values} — only if {cond} fired"


_add_generated_eval_labels()


BINARY_COLUMNS = [c for c in SHEET1_COLUMNS if c.startswith("tech_")] + ["no_frame_present"]


def read_manifest() -> dict[str, dict]:
    """Read the manifest, refusing anything that would move the denominator.

    The dict comprehension this replaces collapsed a duplicate `id` onto whichever
    row came last, and `collect()` merely printed a line for a missing file and
    carried on. Either would silently change the number of paragraphs the thesis
    reports, which is the class of defect this whole module exists to prevent, so
    both now abort. Plan step P1 asked for exactly this and the earlier version
    only enforced it inside the hashing helper.
    """
    with (CORPUS / "manifest.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    problems: list[str] = []
    ids = [r.get("id", "") for r in rows]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append(f"duplicate manifest id(s): {dupes}. One row would silently overwrite "
                        f"the other and the corpus would shrink without a trace.")
    blank = [i for i, r in zip(ids, rows) if not r.get("file", "").strip()]
    if blank:
        problems.append(f"manifest row(s) with no `file` path: {blank}")
    missing = [f"{r['id']} -> {r.get('file')}" for r in rows
               if r.get("file", "").strip() and not (CORPUS / r["file"]).exists()]
    if missing:
        problems.append(f"{len(missing)} manifest row(s) point at a file that does not exist: "
                        f"{missing[:6]}")
    files = [r.get("file", "") for r in rows if r.get("file", "").strip()]
    reused = sorted({f for f in files if files.count(f) > 1})
    if reused:
        problems.append(f"the same file is referenced by more than one manifest row: {reused}. "
                        f"Its paragraphs would be collected twice under different ids.")
    if problems:
        raise SystemExit("manifest.csv is not usable:\n  - " + "\n  - ".join(problems))
    return {r["id"]: r for r in rows}


def text_status(raw: str) -> str:
    """The collector's own declaration of whether body text is present."""
    m = FRONTMATTER.search(raw)
    if not m:
        return "UNKNOWN"
    s = re.search(r"text_status:\s*(.+)", m.group(1))
    return s.group(1).strip() if s else "UNKNOWN"


def body(raw: str, status: str) -> str:
    """Return genuine article prose only, or an empty string."""
    stripped = FRONTMATTER.sub("", raw, count=1)
    kind = status.split()[0].upper()
    if kind == "FULL":
        # the '# ' line is the article's own headline; everything after it is body
        m = re.search(r"^# .*?$", stripped, re.M)
        return stripped[m.end():] if m else ""
    if kind == "PARTIAL":
        m = re.search(r"^## Body text.*?$", stripped, re.M)
        if not m:
            return ""
        rest = stripped[m.end():]
        nxt = re.search(r"^## ", rest, re.M)
        return rest[: nxt.start()] if nxt else rest
    return ""  # EXTRACT ONLY / METADATA ONLY: no prose exists in this file


def segment(text: str) -> list[str]:
    """Split article prose into paragraphs.

    The segmentation rule is a design decision that belongs in Chapter 4, not a
    detail. Blocks shorter than MIN_WORDS words, and blocks that are headings,
    pull-quotes, lists, tables, bold sub-heads or rules, are not paragraphs.
    """
    out = []
    for block in re.split(r"\n\s*\n", text):
        b = block.strip()
        if not b or b[0] in "#>|*-" or len(b.split()) < MIN_WORDS:
            continue
        out.append(re.sub(r"\s+", " ", b))
    return out


def matched_tech(text: str) -> list[str]:
    low = text.lower()
    return [t for t, pat in TECH_PATTERNS.items() if re.search(pat, low)]


def collect() -> tuple[list[dict], list[tuple[str, str, str]]]:
    manifest = read_manifest()
    rows: list[dict] = []
    skipped: list[tuple[str, str, str]] = []
    for aid, m in sorted(manifest.items()):
        path = CORPUS / m["file"]
        if not path.exists():        # read_manifest() already refuses this; belt and braces
            raise SystemExit(f"missing file for {aid}: {m['file']}")
        raw = path.read_text(encoding="utf-8")
        status = text_status(raw)
        prose = body(raw, status)
        if not prose.strip():
            skipped.append((aid, m["outlet"], status.split(" - ")[0].split(",")[0]))
            continue
        for i, text in enumerate(segment(prose), start=1):
            tech = matched_tech(text)
            rows.append({
                "paragraph_id": f"{aid}_p{i:02d}",
                "article_id": aid,
                "lang": m["language"],
                "outlet": m["outlet"],
                "country": m["country"],
                "pub_date": m["publication_date"],
                "headline": m["headline"],
                "paragraph_text": text,
                # The truncated hash stays, because six committed label files join
                # on it. The full digest is what the registry keys on -- a 16-hex
                # prefix is a collision surface with no upside.
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
                "digest_full": sr.digest(text),
                "has_transcription_gap": 1 if GAP_MARKER in text else 0,
                "_matched_tech": tech,     # internal only, never written to a sheet
                "_n_tech": len(tech),
                "_candidate": bool(tech),
                "_selected_for": [s for s in m["selected_for"].split(";") if s],
                "_status": status.split(" - ")[0].split(",")[0],
            })
    return rows, skipped


def assign_splits(rows: list[dict]) -> dict[str, str]:
    """RETIRED. Kept only so that reading git history makes sense.

    This function recomputed membership from mutable article files on every run,
    counting only NEWLY assigned articles against A0_SIZE / A1_SIZE -- so prior
    membership never counted against the target and a rerun quietly refilled both
    development sets. It also read only `by_article`, so the paragraph-level A0b
    marks were invisible to it: that is how three A0b paragraphs ended up in the
    A1 sheet and were coded a second time. Routing now goes through
    split_registry.route(), which looks membership up instead of recomputing it.
    """
    raise RuntimeError(
        "assign_splits() is retired. Splits are looked up, not recomputed -- see "
        "split_registry.route(). Calling this would reintroduce the refill bug.")


def _assign_splits_historical(rows: list[dict]) -> dict[str, str]:
    """The original article-level draw. Never called; preserved for the record.

    Splitting at paragraph level would let the same article -- and therefore the
    same event, the same outlet vocabulary and the same house style -- appear in
    both the block used to tune the prompt and the block reserved for evaluation.
    That is leakage even though no paragraph is literally repeated. The whole
    article moves together, and once assigned it never moves again.
    """
    prior: dict[str, str] = {}
    if SPLIT_FILE.exists():
        prior = json.loads(SPLIT_FILE.read_text(encoding="utf-8")).get("by_article", {})

    articles: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        articles[r["article_id"]].append(r)

    art_split = {a: s for a, s in prior.items() if a in articles}
    if art_split:
        print(f"  · {len(art_split)} articles keep their existing split; never reshuffled")

    def cand_count(aid: str) -> int:
        return sum(1 for r in articles[aid] if r["_candidate"])

    def modes(aid: str) -> set[str]:
        return set(articles[aid][0]["_selected_for"])

    unassigned = sorted(a for a in articles if a not in art_split)

    # A0 -- calibration. Purposive: pick the SMALLEST articles that between them
    # cover the most failure modes, so the calibration cost stays low while the
    # rule-breaking coverage stays high.
    want = {"multi-technology-conflicting-evaluation", "quoted-vs-outlet-voice",
            "no-frame-procedural", "mixed-or-neutral-evaluation",
            "security-without-mechanism", "nuclear-versus-renewables"}
    a0_articles: list[str] = []
    covered: set[str] = set()
    pool = sorted(unassigned, key=lambda a: (len(articles[a]), a))
    while pool and sum(len(articles[a]) for a in a0_articles) < A0_SIZE:
        best = max(pool, key=lambda a: (len(modes(a) & want - covered),
                                        -len(articles[a])))
        if sum(len(articles[a]) for a in a0_articles) + len(articles[best]) > A0_SIZE * 1.6:
            pool.remove(best)
            continue
        a0_articles.append(best)
        covered |= modes(best) & want
        pool.remove(best)
        if covered >= want and sum(len(articles[a]) for a in a0_articles) >= A0_SIZE * 0.7:
            break
    for a in a0_articles:
        art_split[a] = "A0"
    print(f"  · A0 covers failure modes: {', '.join(sorted(covered)) or 'none tagged'}")

    # A1 -- development. Whole articles, language-balanced, until the target size.
    rest = [a for a in unassigned if a not in art_split]
    by_lang = defaultdict(list)
    for a in rest:
        by_lang[articles[a][0]["lang"]].append(a)
    for v in by_lang.values():
        random.shuffle(v)
    a1_articles: list[str] = []
    n = 0
    langs = sorted(by_lang)
    while n < A1_SIZE and any(by_lang[l] for l in langs):
        for l in langs:
            if not by_lang[l] or n >= A1_SIZE:
                continue
            a = by_lang[l].pop()
            a1_articles.append(a)
            n += len(articles[a])
    for a in a1_articles:
        art_split[a] = "A1"

    for a in articles:
        art_split.setdefault(a, "RESERVED_A2")

    assign_splits.by_article = art_split          # stashed for the split file
    return {r["text_sha256"]: art_split[r["article_id"]] for r in rows}


def blank_row(r: dict) -> dict:
    row = {c: "" for c in SHEET1_COLUMNS}
    row.update({k: r[k] for k in (
        "paragraph_id", "article_id", "lang", "outlet", "country", "pub_date",
        "headline", "paragraph_text", "text_sha256")})
    for c in BINARY_COLUMNS:
        row[c] = 0                      # you only ever type 1s
    row["annotator"] = ANNOTATOR
    # Explicit provenance beats a substring guess. finalize.py refuses a sheet
    # whose annotator_type is `model` unless --annotator-is-a-model is passed;
    # a sheet exported by a coding interface that fills labels itself has to
    # declare that here rather than be caught by a name heuristic.
    row["annotator_type"] = "human"
    row["codebook_version"] = CODEBOOK_VERSION
    return row


# Only the code that can change a paragraph's identity, its candidate status or
# its routing is bound. Binding every .py in the directory meant editing the test
# suite tripped the drift guard and blocked the build — a check that fires on
# changes it has no reason to care about gets switched off, so it is scoped to
# what actually matters: segmentation and keyword matching live in build_sheets,
# containment and routing in split_registry.
IDENTITY_CODE = ("build_sheets.py", "split_registry.py")

# The manifest fields that feed a reported number. Binding the whole manifest file
# hash was wrong in both directions: it fired on a new row (growth, which is fine)
# and it said nothing about WHICH article's metadata moved. Per-article metadata
# is what a language, outlet or country count is computed from, so that is what a
# version binds.
COUNT_BEARING_FIELDS = ("language", "outlet", "country", "publication_date")


def _article_meta_hash(row: dict) -> str:
    joined = "|".join(str(row.get(f, "")) for f in COUNT_BEARING_FIELDS)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]



def _provenance() -> dict:
    """What computed these identities. Not a `producing_commit`: an artifact cannot
    contain the SHA of the commit that first contains it, so this is the revision
    the run started FROM, plus content hashes of the code that ran. The corpus
    itself is bound by content in _input_binding, because `pilot-corpus` is
    entirely gitignored and no commit can bind it."""
    import platform
    import subprocess

    def git(*a: str) -> str:
        try:
            return subprocess.run(["git", *a], cwd=str(ROOT), capture_output=True,
                                  text=True, timeout=30).stdout.strip()
        except Exception:
            return ""

    here = Path(__file__).resolve().parent
    return {
        "code_input_commit": git("rev-parse", "HEAD") or "unknown",
        "code_input_commit_short": git("rev-parse", "--short", "HEAD") or "unknown",
        "code_tree_dirty_for_tools_annotation": [
            l for l in git("status", "--porcelain", "--", "tools/annotation").splitlines() if l],
        "code_file_sha256": {n: hashlib.sha256((here / n).read_bytes()).hexdigest()[:16]
                             for n in IDENTITY_CODE if (here / n).exists()},
        "python": platform.python_version(),
        "algorithm_version": "1",
        "min_words": MIN_WORDS,
        "seed": SEED,
        "runtime": sr.runtime_fingerprint(),
    }


def _input_binding(manifest: dict) -> dict:
    """Bind a corpus version to its inputs by content: the manifest plus exactly
    the files it references. `articles/` holds more files than the manifest names,
    so hashing the directory would bind things this version never read."""
    files = {}
    for aid in sorted(manifest):
        rel = manifest[aid].get("file", "")
        q = CORPUS / rel
        if rel and q.exists():
            files[rel] = hashlib.sha256(q.read_bytes()).hexdigest()[:16]
    return {
        # Recorded for the record, NOT used as a drift trigger: a new manifest row
        # changes it, and that is legitimate growth.
        "manifest_sha256": hashlib.sha256((CORPUS / "manifest.csv").read_bytes()).hexdigest()[:16],
        "manifest_articles": len(manifest),
        "article_meta_sha256": {aid: _article_meta_hash(manifest[aid]) for aid in sorted(manifest)},
        "count_bearing_fields": list(COUNT_BEARING_FIELDS),
        "referenced_files": len(files),
        "file_sha256": files,
        "binding_note": ("Bound by content, not by commit: the corpus is gitignored. These "
                         "hashes establish that the present files still yield this version's "
                         "identities, which verify_closed_versions() checks on every run. They "
                         "do not make a lost file reconstructable -- reproducible archival is a "
                         "separate, unsolved prerequisite."),
    }


def verify_input_bindings(registry, manifest: dict) -> list[dict]:
    """Compare recorded input bindings against what is present now.

    Returns STRUCTURED records, not sentences. An acceptance keyed to a sentence
    also accepts the next change that happens to read the same way -- A004's
    language moving to `en` and later to `fr` produce an identical note -- so each
    record carries (version, component, key, recorded, observed) and is accepted
    by a fingerprint over exactly that.

    Identity checks do not cover any of this. Editing a count-bearing manifest
    field, changing the segmentation or containment code, or moving to an
    interpreter with a different Unicode database leaves every paragraph identity
    intact while shifting a reported number.
    """
    out: list[dict] = []
    here = Path(__file__).resolve().parent

    def rec(v, component, key, recorded, observed):
        out.append({"version": v, "component": component, "key": key,
                    "recorded": str(recorded), "observed": str(observed)})

    for v in registry.corpus_versions:
        name = v.get("version", "?")
        b_ = v.get("input_binding") or {}
        own = {m["article_id"] for m in v.get("members", [])}

        # count-bearing metadata, only for the articles this version contains
        rec_meta = b_.get("article_meta_sha256") or {}
        for aid in sorted(own):
            if aid not in manifest:
                rec(name, "article_meta", aid, rec_meta.get(aid, "<bound>"), "<gone from manifest>")
            elif aid not in rec_meta:
                rec(name, "article_meta", aid, "<unbound>", _article_meta_hash(manifest[aid]))
            elif rec_meta[aid] != _article_meta_hash(manifest[aid]):
                rec(name, "article_meta", aid, rec_meta[aid], _article_meta_hash(manifest[aid]))

        # article content. Required: check_versions() refuses a version with no
        # file_sha256 at all, and every article this version owns must be bound.
        rec_files = b_.get("file_sha256") or {}
        owned_paths = {manifest[a]["file"] for a in own if a in manifest}
        for relp in sorted(owned_paths - set(rec_files)):
            rec(name, "file", relp, "<unbound>", "present")
        for relp, h in sorted(rec_files.items()):
            q = CORPUS / relp
            if not q.exists():
                rec(name, "file", relp, h, "<missing>")
            elif hashlib.sha256(q.read_bytes()).hexdigest()[:16] != h:
                rec(name, "file", relp, h, hashlib.sha256(q.read_bytes()).hexdigest()[:16])

        prov = v.get("provenance") or {}

        # the runtime the containment decision depends on
        rec_rt, cur_rt = prov.get("runtime") or {}, sr.runtime_fingerprint()
        if not rec_rt:
            rec(name, "runtime", "*", "<unbound>", "present")
        else:
            for k in sorted(cur_rt):
                if str(rec_rt.get(k)) != str(cur_rt[k]):
                    rec(name, "runtime", k, rec_rt.get(k), cur_rt[k])

        # the code that computes identities and routing
        rec_code = prov.get("code_file_sha256") or {}
        if not rec_code:
            rec(name, "code", "*", "<unbound>", "present")
        for n in IDENTITY_CODE:
            q = here / n
            if n not in rec_code:
                rec(name, "code", n, "<unbound>",
                    hashlib.sha256(q.read_bytes()).hexdigest()[:16] if q.exists() else "<missing>")
            elif not q.exists():
                rec(name, "code", n, rec_code[n], "<missing>")
            elif hashlib.sha256(q.read_bytes()).hexdigest()[:16] != rec_code[n]:
                rec(name, "code", n, rec_code[n],
                    hashlib.sha256(q.read_bytes()).hexdigest()[:16])
    return out


LABELS = Path(__file__).resolve().parent / "labels"
CODEBOOK_FILE = Path(__file__).resolve().parent / "codebook" / f"codebook-v{CODEBOOK_VERSION}.yaml"
SHEET_SCHEMA = 2      # 1 = the six committed sessions (no manifest, no pass-2 hash)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


LEGACY_SESSIONS = ("A0", "A0b", "A1")     # the sessions finalized before manifests existed


def codebook_sha_for(version: str) -> str | None:
    q = Path(__file__).resolve().parent / "codebook" / f"codebook-v{version}.yaml"
    return _sha(q) if q.exists() else None


def write_session_manifest(session: str, rows: list[dict], registry, *, kind: str,
                           reconstructed: dict | None = None, passes: dict | None = None) -> Path:
    """One manifest per session, beside the label files, IDs and hashes only.

    It establishes which text a session was coded against: every paragraph with its
    identity triple, its split, and the registry head at generation time, so that a
    later label file can be checked against it (finalize.py) rather than trusted.
    Written when a sheet is generated; RECONSTRUCTED for the six sessions that
    predate it, and then marked so, with the evidence named.
    """
    LABELS.mkdir(exist_ok=True)
    out = LABELS / f"{session}_manifest.json"
    if out.exists():
        try:
            prev = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = {}
        if not prev.get("reconstructed"):
            sys.exit(f"REFUSING to overwrite labels/{out.name}: it is a current-schema session manifest "
                     f"(the provenance of a sheet). A manifest is written once.")
    here = Path(__file__).resolve()
    # The codebook a session was coded under is per PASS for the legacy sessions (A0: 0.1 then
    # 0.3; A0b: 0.2 then 0.3); a current session has one frozen version.
    if passes is None:
        passes = {"pass1": {"codebook_version": CODEBOOK_VERSION, "codebook_sha256": codebook_sha_for(CODEBOOK_VERSION)},
                  "pass2": {"codebook_version": CODEBOOK_VERSION, "codebook_sha256": codebook_sha_for(CODEBOOK_VERSION)}}
    versions = sorted({v["codebook_version"] for v in passes.values()})
    doc = {
        "session": session,
        "sheet_kind": kind,
        "sheet_schema": SHEET_SCHEMA if reconstructed is None else 1,
        "generated_at": date.today().isoformat(),
        "codebook_versions": versions,
        "passes": passes,
        "registry_head_hash": (registry.assignment_log[-1].get("h")
                               if registry.assignment_log else None),
        "generator": {"file": here.name, "sha256": _sha(here)},
        "n_paragraphs": len(rows),
        "paragraphs": [
            {"paragraph_id": r["paragraph_id"], "article_id": r["article_id"],
             "text_sha256": r["text_sha256"], "split": r.get("split", ""),
             "lang": r.get("lang", ""), "outlet": r.get("outlet", "")}
            for r in sorted(rows, key=lambda x: x["paragraph_id"])],
        "note": ("IDs and hashes only -- no article text. A label file of this session is "
                 "valid only if every row's text hash is in this list; finalize.py checks "
                 "that by recomputing the hash from the sheet, not by reading the cell."),
    }
    if reconstructed is not None:
        doc["reconstructed"] = True
        doc["reconstruction"] = reconstructed
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def reconstruct_manifests(registry, all_rows: list[dict]) -> None:
    """Retrospective manifests for the sessions finalized before manifests existed.

    Evidence: the committed label files (row identities) joined to
    ALL_paragraphs.csv identities that the registry has just validated. The
    pass-1 → pass-2 key comparison the current schema enforces is run once here and
    RECORDED, not repaired: A1's pass 2 carries rows from three paragraphs whose
    registry split is A0b, and that is a fact about the session, not a defect to
    edit away.
    """
    by_pid = {r["paragraph_id"]: r for r in all_rows}
    assigned = registry.current_splits()
    split_of = {k[1]: v for k, v in assigned.items()}
    sessions_csv = LABELS / "sessions.csv"
    recs = list(csv.DictReader(sessions_csv.open(encoding="utf-8-sig"), delimiter=DELIM))
    by_session: dict[str, dict[str, dict]] = defaultdict(dict)
    for rec in recs:
        base, _, pas = rec["session"].rpartition("_")
        if base not in LEGACY_SESSIONS:        # never a current-schema session (round-1 inspection #3)
            continue
        by_session[base][pas] = rec
    for base, sess in sorted(by_session.items()):
        pids: set[str] = set()
        evidence = {}
        pass_info = {}
        keys1: set[str] = set()
        keys2: set[str] = set()
        for pas, rec in sess.items():
            f = LABELS / rec["labels_file"]
            rows = list(csv.DictReader(f.open(encoding="utf-8-sig"), delimiter=DELIM))
            evidence[pas] = {"labels_file": rec["labels_file"],
                             "labels_sha256_recorded": rec["labels_sha256"],
                             "labels_sha256_now": _sha(f)[:16], "n_rows": len(rows)}
            pass_info[pas] = {"codebook_version": rec["codebook_version"],
                              "codebook_sha256": codebook_sha_for(rec["codebook_version"]),
                              "labels_file": rec["labels_file"], "labels_sha256": rec["labels_sha256"]}
            for r in rows:
                pids.add(r["paragraph_id"])
                if pas == "pass1":
                    for c in r:
                        if c.startswith("tech_") and (r[c] or "").strip() == "1":
                            keys1.add(f"{r['paragraph_id']}__{c[len('tech_'):]}")
                elif pas == "pass2":
                    keys2.add(f"{r['paragraph_id']}__{r['technology']}")
        missing = sorted(p for p in pids if p not in by_pid)
        rows = []
        for pid in sorted(pids):
            if pid in by_pid:
                r = dict(by_pid[pid]); r["split"] = split_of.get(pid, r.get("split", ""))
                rows.append(r)
        recon = {
            "method": ("paragraph_id from the committed label files, joined to the "
                       "registry-validated identities in ALL_paragraphs.csv"),
            "evidence": evidence,
            "paragraphs_not_resolvable": missing,
            "splits_present": dict(Counter(r["split"] for r in rows)),
            "pass1_keys": sorted(keys1) if keys1 else None,
            "pass2_keys": sorted(keys2) if keys2 else None,
            "key_check": (
                {"equal": keys1 == keys2,
                 "only_in_pass1": sorted(keys1 - keys2), "only_in_pass2": sorted(keys2 - keys1)}
                if keys1 and keys2 else "not applicable (one pass)"),
        }
        out = write_session_manifest(base, rows, registry, kind="reconstructed",
                                     reconstructed=recon, passes=pass_info)
        kc = recon["key_check"]
        print(f"  reconstructed {out.name}: {len(rows)} paragraphs, splits "
              f"{recon['splits_present']}"
              + (f", pass-1/pass-2 keys equal={kc['equal']}" if isinstance(kc, dict) else ""))
        if missing:
            print(f"    ! {len(missing)} paragraph id(s) not resolvable: {missing[:5]}")


# ---- Step 1.2: the corpus archive ------------------------------------------------ #
# The archive lives under tools/data/ (gitignored), NOT inside the corpus folder: that
# folder is read-only for every agent by the repository's standing rule, and the plan's
# original location was corrected for that reason. The tracked record is the manifest
# beside the label files.
ARCHIVE_DIR = ROOT / "tools" / "data" / "archive"
ARCHIVE_MANIFEST = Path(__file__).resolve().parent / "archive-manifest.json"
ARCHIVED_CODE = ("build_sheets.py", "split_registry.py", "expand_sheet2.py", "finalize.py")


def _git(*a: str) -> str:
    try:
        return subprocess.run(["git", *a], cwd=str(ROOT), capture_output=True, text=True,
                              timeout=30).stdout.strip()
    except Exception:
        return ""


def archive_corpus(registry, manifest: dict) -> Path:
    """Tar the gitignored sources plus everything needed to re-derive the identities.

    Stated honestly in the manifest it writes: this preserves the CURRENT realisation
    of the sources, which the registry accepted after binding-drift records; the bytes
    corpus version v2 was originally built from are not recoverable from here.
    """
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    versions = [v["version"] for v in registry.corpus_versions]
    name = f"corpus_{'-'.join(versions)}_{stamp}.tar.gz"
    tar_path = ARCHIVE_DIR / name
    here = Path(__file__).resolve().parent
    files: dict[str, str] = {}
    with tarfile.open(tar_path, "w:gz") as tar:
        for aid in sorted(manifest):
            rel = manifest[aid].get("file", "")
            q = CORPUS / rel
            if rel and q.exists():
                tar.add(q, arcname=f"pilot-corpus/{rel}")
                files[f"pilot-corpus/{rel}"] = hashlib.sha256(q.read_bytes()).hexdigest()
            # the author's print is the transcription's evidence; archive it if present
            for extra in (q.with_suffix(".pdf"), q.with_name(q.stem + "_print.pdf")):
                if extra.exists():
                    tar.add(extra, arcname=f"pilot-corpus/{extra.relative_to(CORPUS)}")
                    files[f"pilot-corpus/{extra.relative_to(CORPUS)}"] = hashlib.sha256(
                        extra.read_bytes()).hexdigest()
        for extra in (CORPUS / "manifest.csv", SPLIT_FILE):
            tar.add(extra, arcname=f"inputs/{extra.name}")
            files[f"inputs/{extra.name}"] = hashlib.sha256(extra.read_bytes()).hexdigest()
        for code in ARCHIVED_CODE:
            q = here / code
            if q.exists():
                tar.add(q, arcname=f"code/{code}")
                files[f"code/{code}"] = hashlib.sha256(q.read_bytes()).hexdigest()
    accepted = sum(len(e.get("records", [])) for e in registry.assignment_log
                   if e.get("kind") == sr.EVENT_DRIFT)
    record = {
        "created": stamp,
        "tarball": {"path": (str(tar_path.relative_to(ROOT)) if tar_path.is_relative_to(ROOT)
                             else str(tar_path)),
                    "sha256": hashlib.sha256(tar_path.read_bytes()).hexdigest(),
                    "bytes": tar_path.stat().st_size, "n_members": len(files)},
        "files_sha256": files,
        "corpus_versions": [{"version": v["version"], "created": v["created"],
                             "member_count": v["member_count"], "checksum": v["checksum"]}
                            for v in registry.corpus_versions],
        "registry_head_hash": registry.assignment_log[-1].get("h"),
        "accepted_binding_drift_records": accepted,
        "git": {"commit": _git("rev-parse", "HEAD") or "unknown",
                "dirty": bool(_git("status", "--porcelain"))},
        "interpreter": {"python": platform.python_version(), **sr.runtime_fingerprint()},
        "disclosure": (
            "This archive preserves the sources as they exist today, which the registry "
            "accepted after the binding-drift records counted above. The bytes that corpus "
            "version v2 was originally derived from differ for 42 article files and are NOT "
            "recoverable from this archive. Copying the tarball off this machine is the "
            "author's act; the archive directory is gitignored because it carries licensed text."),
    }
    ARCHIVE_MANIFEST.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
    return tar_path


# ---- Step 1.5: the frame manifest and the freeze -------------------------------- #
FRAME_MANIFEST = LABELS / "A2_frame_manifest.json"
DIRECT_EXEMPLAR_ARTICLES = ("A011",)   # A011_p02 is quoted in the production prompt (D8)


def canonical_digest(doc: dict) -> str:
    """sha256 of the canonical JSON of `doc` with its own `digest` field excluded."""
    body = {k: v for k, v in doc.items() if k != "digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def eligible_frame(rows: list[dict], assigned: dict, registry) -> list[dict]:
    """The A2 sampling frame: reserve, candidate, gap-free, not a direct-exemplar
    article. Exposure class per paragraph from the registry's closed/mixed articles."""
    exposed = registry.closed_article_ids() | registry.mixed_article_ids()
    out = []
    for r in rows:
        k = (r["article_id"], r["paragraph_id"], r["digest_full"])
        if assigned.get(k) != sr.RESERVE or not r["_candidate"] or r["has_transcription_gap"]:
            continue
        aid = r["article_id"]
        if aid in DIRECT_EXEMPLAR_ARTICLES:
            continue
        out.append({"article_id": aid, "paragraph_id": r["paragraph_id"],
                    "digest": r["digest_full"], "text_sha256": r["text_sha256"],
                    "lang": r["lang"], "outlet": r["outlet"], "country": r["country"],
                    "exposure": ("sibling_calibration_article" if aid in exposed
                                 else "clean_article")})
    return sorted(out, key=lambda x: x["paragraph_id"])


def write_frame_manifest(frame: list[dict], registry) -> dict:
    strata = Counter((f["lang"], f["outlet"]) for f in frame)
    here = Path(__file__).resolve()
    doc = {
        "kind": "A2_frame_manifest",
        "created": date.today().isoformat(),
        "eligibility_rules": [
            f"split == {sr.RESERVE} at the time of writing",
            "candidate: the keyword prefilter marks at least one technology",
            "gap-free: has_transcription_gap is false",
            f"article not in {list(DIRECT_EXEMPLAR_ARTICLES)} (direct exemplar in the prompt)",
        ],
        "exposure_classes": {
            "direct_exemplar_article": list(DIRECT_EXEMPLAR_ARTICLES),
            "sibling_calibration_article": sorted(
                registry.closed_article_ids() | registry.mixed_article_ids()),
        },
        "n_eligible": len(frame),
        "strata": [{"lang": l, "outlet": o, "N_h": n}
                   for (l, o), n in sorted(strata.items())],
        "paragraphs": frame,
        "pre_freeze_head": registry.assignment_log[-1].get("h"),
        "eligible_checksum": sr.eligible_checksum_of(registry.current_splits()),
        "generator": {"file": here.name, "sha256": _sha(here)},
        "interpreter": {"python": platform.python_version(), **sr.runtime_fingerprint()},
    }
    doc["digest"] = canonical_digest(doc)
    FRAME_MANIFEST.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    return doc


def verify_archive(registry) -> list[str]:
    """The archive record exists, the tarball it names is present with the recorded sha256,
    and the corpus versions it binds are the registry's. An existence check alone let an
    empty `{}` pass (review round 6)."""
    if not ARCHIVE_MANIFEST.exists():
        return ["no archive manifest"]
    try:
        rec = json.loads(ARCHIVE_MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"archive manifest is not valid JSON: {exc}"]
    out = []
    tb = rec.get("tarball") or {}
    tar_path = (Path(tb["path"]) if tb.get("path") and Path(tb["path"]).is_absolute()
                else ROOT / tb.get("path", "")) if tb.get("path") else None
    if not tar_path or not tar_path.exists():
        out.append("the tarball named in the archive manifest does not exist")
    elif hashlib.sha256(tar_path.read_bytes()).hexdigest() != tb.get("sha256"):
        out.append("the tarball's sha256 differs from the archive manifest")
    want = {v["version"]: v["checksum"] for v in registry.corpus_versions}
    have = {v.get("version"): v.get("checksum") for v in rec.get("corpus_versions", [])}
    if want != have:
        out.append(f"archived corpus versions {sorted(have)} do not match the registry's {sorted(want)}")
    if not rec.get("files_sha256"):
        out.append("the archive manifest lists no files")
    if registry.assignment_log and rec.get("registry_head_hash") != registry.assignment_log[-1].get("h"):
        out.append("the registry head has moved since the archive was written; re-archive")
    here = Path(__file__).resolve().parent
    for name, digest in (rec.get("files_sha256") or {}).items():
        if name.startswith("code/"):
            q = here / name[len("code/"):]
            if not q.exists() or hashlib.sha256(q.read_bytes()).hexdigest() != digest:
                out.append(f"archived {name} differs from the code about to freeze; re-archive")
        elif name.startswith("pilot-corpus/") and name.endswith(".md"):
            q = CORPUS / name[len("pilot-corpus/"):]
            if not q.exists() or hashlib.sha256(q.read_bytes()).hexdigest() != digest:
                out.append(f"archived {name} differs from the current source; re-archive")
        elif name == "inputs/manifest.csv":      # language, outlet, country come from here
            if hashlib.sha256((CORPUS / "manifest.csv").read_bytes()).hexdigest() != digest:
                out.append("archived manifest.csv differs from the current one; re-archive")
    if "inputs/manifest.csv" not in (rec.get("files_sha256") or {}):
        out.append("the archive does not contain inputs/manifest.csv")
    return out


def freeze_a2(registry, rows: list[dict], assigned: dict) -> dict:
    """Write the frame manifest, then append the terminal freeze whose audit_ref is
    the manifest's digest. Refuses without an archive manifest (Step 1.2 first) and
    refuses if the reserve is already frozen."""
    problems = verify_archive(registry)
    if problems:
        sys.exit("REFUSING to freeze: the archive does not stand.\n  - " + "\n  - ".join(problems)
                 + "\nRun --archive first; the freeze is the collection cutoff and the sources "
                 "must be archived, and verifiably so, before it.")
    if registry.a2_frozen() is not None:
        sys.exit("REFUSING: the evaluation set is already frozen.")
    if FRAME_MANIFEST.exists():
        sys.exit(f"REFUSING: {FRAME_MANIFEST.name} exists. A frame manifest is written once.")
    # The freeze is terminal: nothing can be appended after it, so an acceptance that
    # is not on the log before the freeze can never be recorded at all.
    unaccepted = [r for r in verify_input_bindings(registry, read_manifest())
                  if sr.drift_fingerprint(r) not in registry.accepted_drift()]
    if unaccepted:
        sys.exit("REFUSING to freeze: unaccepted binding drift would be buried under the "
                 "terminal event and could never be recorded afterwards:\n  - "
                 + "\n  - ".join(sr.format_drift(r) for r in unaccepted[:10])
                 + "\nRun --archive --accept-binding-drift (acceptance first, then the archive), "
                 "then --freeze-a2 again.")
    frame = eligible_frame(rows, assigned, registry)
    doc = write_frame_manifest(frame, registry)
    registry.freeze_a2(doc["eligible_checksum"], audit_ref=doc["digest"])
    registry.save(SPLIT_FILE)
    return doc


def write_a2_sheets(sample: dict, rows: list[dict], assigned: dict, registry) -> None:
    """The A2 sheet-1 from the pre-registered sample manifest, in the manifest's
    (seeded, shuffled) order, plus the empty sheet-2 and the A2 session manifest.
    Refuses if a sheet exists: an A2 sheet is written once."""
    if sample.get("kind") != "A2_sample_manifest":
        sys.exit("not an A2 sample manifest")
    # the chain: sample digest recomputes; frame manifest recomputes and is the one the sample
    # names; the registry head is the freeze whose audit_ref is that frame digest
    if canonical_digest(sample) != sample.get("digest"):
        sys.exit("REFUSING: the sample manifest's content does not match its digest.")
    if not FRAME_MANIFEST.exists():
        sys.exit("REFUSING: no frame manifest.")
    frame = json.loads(FRAME_MANIFEST.read_text(encoding="utf-8"))
    if canonical_digest(frame) != frame.get("digest") or frame["digest"] != sample.get("frame_manifest_digest"):
        sys.exit("REFUSING: the frame manifest does not match its digest or is not the one the sample was drawn from.")
    import draw_a2 as _da
    try:
        _da.verify_freeze(frame, registry)
    except _da.DrawError as exc:
        sys.exit(f"REFUSING: {exc}")
    name = "A2_evaluation"
    if (OUT / f"{name}_sheet-1.csv").exists():
        sys.exit(f"REFUSING: sheets/{name}_sheet-1.csv exists; the A2 sheet is generated once.")
    by_key = {(r["article_id"], r["paragraph_id"], r["digest_full"]): r for r in rows}
    sub = []
    for sp in sample["paragraphs"]:
        k = (sp["article_id"], sp["paragraph_id"], sp["digest"])
        r = by_key.get(k)
        if r is None:
            sys.exit(f"REFUSING: sampled identity {k[:2]} is not present in the current corpus.")
        if assigned.get(k) != sr.RESERVE:
            sys.exit(f"REFUSING: sampled paragraph {sp['paragraph_id']} is not in the reserve.")
        r = dict(r); r["split"] = "A2"
        sub.append(r)
    OUT.mkdir(exist_ok=True)
    with (OUT / f"{name}_sheet-1.csv").open("w", newline="", encoding=ENC) as f:
        w = csv.DictWriter(f, fieldnames=SHEET1_COLUMNS, delimiter=DELIM)
        w.writeheader()
        for r in sub:                       # manifest order = the pre-registered sitting order
            w.writerow(blank_row(r))
    with (OUT / f"{name}_sheet-2.csv").open("w", newline="", encoding=ENC) as f:
        w2 = csv.writer(f, delimiter=DELIM)
        w2.writerow(SHEET2_COLUMNS)
        w2.writerow([SHEET2_LABELS.get(c, "") for c in SHEET2_COLUMNS])
    mpath = write_session_manifest("A2", sub, registry, kind="sheet-1")
    print(f"wrote sheets/{name}_sheet-1.csv   {len(sub)} rows to code, in manifest order")
    print(f"wrote sheets/{name}_sheet-2.csv   empty -- fill after pass 1 (expand_sheet2.py)")
    print(f"wrote labels/{mpath.name}   session manifest; commit it before opening the sheet")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="print counts, write nothing")
    ap.add_argument("--accept-binding-drift", action="store_true",
                    help="record, rather than refuse, changed inputs for a recorded version")
    ap.add_argument("--archive", action="store_true",
                    help="tar the gitignored sources, inputs and code into tools/data/archive/ "
                         "and write the tracked archive-manifest.json; the only registry write "
                         "it makes is a pending --accept-binding-drift, recorded BEFORE archiving")
    ap.add_argument("--freeze-a2", action="store_true",
                    help="write labels/A2_frame_manifest.json and append the TERMINAL a2_frozen "
                         "event whose audit_ref is the manifest digest (requires --archive first)")
    ap.add_argument("--from-manifest", metavar="SAMPLE_MANIFEST",
                    help="generate the A2 sheet-1 (and empty sheet-2) from a sample manifest "
                         "written by draw_a2.py, plus the A2 session manifest")
    ap.add_argument("--reconstruct-manifests", action="store_true",
                    help="write retrospective session manifests for the finalized sessions "
                         "from the committed label files (marked reconstructed); writes "
                         "nothing else")
    args = ap.parse_args()

    rows, skipped = collect()
    manifest_n = len(read_manifest())

    print(f"pilot corpus: {manifest_n} articles in the manifest")
    if skipped:
        by_status = Counter(s for _, _, s in skipped)
        print(f"  · {len(skipped)} carry NO body prose and are excluded: " +
              ", ".join(f"{v}× {k}" for k, v in by_status.most_common()))
    with_prose = sorted({r["article_id"] for r in rows})
    print(f"  · {len(with_prose)} articles contain usable prose: {', '.join(with_prose)}")

    cand = [r for r in rows if r["_candidate"]]
    multi = [r for r in rows if r["_n_tech"] > 1]
    print(f"\nparagraphs (>= {MIN_WORDS} words) : {len(rows)}")
    print(f"  candidates (>=1 technology)  : {len(cand)} ({len(cand)/max(len(rows),1):.0%})")
    print(f"  multi-technology             : {len(multi)} ({len(multi)/max(len(cand),1):.0%} of candidates)")
    for axis in ("lang", "country", "outlet"):
        print(f"  by {axis:<8}: " + "  ".join(
            f"{k}={v}" for k, v in Counter(r[axis] for r in rows).most_common()))

    # ---- P4: every recorded identity must still resolve, before anything else #
    registry = sr.SplitRegistry.load(SPLIT_FILE)
    sr.verify_closed_versions(registry, rows)
    print(f"\nregistry: schema {registry.schema_version}, "
          f"versions {[v['version'] for v in registry.corpus_versions]}, "
          f"{len(registry.members())} recorded identities, chain intact")

    # ---- P3 / P3a / P5: route ---------------------------------------------- #
    routed = sr.route(registry, rows)
    assigned, quarantined = routed["assigned"], routed["quarantined"]
    st = routed["stats"]
    by_key = {(r["article_id"], r["paragraph_id"], r["digest_full"]): r for r in rows}

    print(f"  closed articles : {', '.join(st['closed_articles'])}")
    print(f"  mixed  articles : {', '.join(st['mixed_articles'])}")
    print(f"  containment     : {st['shingle_words']}-word shingles, "
          f">= {st['containment_threshold']} quarantines")
    drift = verify_input_bindings(registry, read_manifest())
    if drift:
        for r in drift[:10]:
            print(f"  BINDING DRIFT   : {sr.format_drift(r)}")
        if len(drift) > 10:
            print(f"  BINDING DRIFT   : … {len(drift) - 10} more")
        already = registry.accepted_drift()
        fresh_drift = [r for r in drift if sr.drift_fingerprint(r) not in already]
        # After the terminal freeze nothing can be appended, so an acceptance is impossible and
        # code-hash drift of this generator is DISCLOSED, not refused: the frozen frame is bound by
        # its digest, the sample by the frame's, and the sheets are generated from the identities
        # those name (test_split_registry L15b-e). Drift of the INPUTS keeps refusing.
        if registry.a2_frozen() is not None:
            if args.accept_binding_drift:
                sys.exit("REFUSING: --accept-binding-drift after the terminal freeze; nothing can be appended "
                         "to the assignment log any more. Code-hash drift is disclosed in labels/README.md.")
            code_drift = [r for r in fresh_drift if r.get("component") == "code"]
            if code_drift:
                print(f"  DISCLOSED       : {len(code_drift)} code-hash record(s) drifted since the freeze "
                      f"(generator moved on; frame and sample are digest-bound) -- not acceptable, not refused")
            fresh_drift = [r for r in fresh_drift if r.get("component") != "code"]
        # `--report` writes nothing, so it must never refuse. Refusing there made the
        # diagnostic unavailable in exactly the situation you would reach for it --
        # found while tidying, when a docstring edit changed the bound code hash and
        # `--report` stopped answering questions about the state it was reporting on.
        if (fresh_drift and not args.accept_binding_drift and not args.report
                and not args.reconstruct_manifests and not args.archive):  # no registry write
            sys.exit(
                "\nREFUSING to write: the inputs a recorded corpus version was built from have "
                "changed.\nIdentities may still all resolve while metadata-derived counts "
                "(language, outlet, country, candidate, gap) move — which is a reported number "
                "changing without a trace.\nRe-run with --accept-binding-drift to record the "
                "acceptance as a durable event in the assignment log.")
        if not fresh_drift:
            print(f"  (all {len(drift)} drift record(s) were accepted earlier and are on record)")
        # Recorded HERE, before any branch below, and saved at once. The acceptance used
        # to be appended only on the default (new-members) path, after `--freeze-a2`
        # had already returned: the flag bypassed the refusal, the terminal freeze was
        # appended, and the acceptance was never written -- found on the real freeze of
        # 2026-09-06, where the three code-hash records now sit unaccepted under the
        # terminal event (see labels/README.md). `--report`, `--archive` and
        # `--reconstruct-manifests` write no registry state, so they record nothing.
        # `--archive` DOES record it: the archive must be taken after the acceptance,
        # because the freeze verifies that the registry head has not moved since the
        # archive, and the acceptance moves it.
        if (fresh_drift and args.accept_binding_drift
                and not (args.report or args.reconstruct_manifests)):
            registry.append_drift_acceptance(
                fresh_drift, audit_ref=f"build_sheets-{date.today().isoformat()}")
            registry.save(SPLIT_FILE)
            print(f"  DEVIATION       : --accept-binding-drift, {len(fresh_drift)} record(s) "
                  f"fingerprinted and recorded as a durable log event, before any other write")
            fresh_drift = []

    print("\nsplit assignment (looked up, never recomputed):")
    for split in sorted({*assigned.values()}, key=lambda s: (s == sr.RESERVE, s)):
        keys = [k for k, v in assigned.items() if v == split]
        rs = [by_key[k] for k in keys]
        cand = [r for r in rs if r["_candidate"]]
        gaps = [r for r in cand if r["has_transcription_gap"]]
        arts = sorted({r["article_id"] for r in rs})
        langs = " ".join(f"{k}:{v}" for k, v in sorted(Counter(r["lang"] for r in rs).items()))
        print(f"  {split:<14} {len(arts):>2} articles  {len(rs):>4} paragraphs "
              f"({len(cand)} candidates, {len(gaps)} gap-marked)   {langs}")
        print(f"                 {', '.join(arts)}")
    if quarantined:
        print(f"\n  QUARANTINE_ADJUDICATE  {len(quarantined)} paragraph(s), held for "
              f"adjudication — recorded members, not in the reserve:")
        for k, why in list(quarantined.items())[:12]:
            print(f"      {k[1]:<22} {why}")
        if len(quarantined) > 12:
            print(f"      … {len(quarantined) - 12} more")
    else:
        print("\n  QUARANTINE_ADJUDICATE  empty")

    # ---- P7: assigned + quarantined partition the corpus ------------------- #
    # Quarantine is a split INSIDE `assigned`, so the old `assigned + quarantined`
    # sum was wrong the moment a quarantine existed -- it would have aborted every
    # build with a non-empty quarantine, before any membership or log event was
    # written. It passed only because the quarantine is currently empty.
    n_q = sum(1 for s in assigned.values() if s == sr.QUARANTINE)
    if len(assigned) != len(rows) or n_q != len(quarantined):
        sys.exit(f"INVARIANT VIOLATED: {len(assigned)} assigned of {len(rows)} collected, "
                 f"{n_q} quarantine-split vs {len(quarantined)} reasons")
    print(f"\n  partition check : {len(assigned)} paragraphs, each with exactly one split "
          f"({n_q} quarantined) = {len(rows)} collected  OK")

    if args.reconstruct_manifests:
        for r in rows:
            r["split"] = assigned.get((r["article_id"], r["paragraph_id"], r["digest_full"]),
                                      sr.QUARANTINE)
        print("\nsession manifests (retrospective):")
        reconstruct_manifests(registry, rows)
        return
    if args.archive:
        tar_path = archive_corpus(registry, read_manifest())
        print(f"\narchived {tar_path.relative_to(ROOT)}  ->  record in "
              f"{ARCHIVE_MANIFEST.relative_to(ROOT)}   commit the record, copy the tarball off "
              f"this machine")
        return
    if args.freeze_a2:
        doc = freeze_a2(registry, rows, assigned)
        print(f"\nwrote labels/{FRAME_MANIFEST.name}: {doc['n_eligible']} eligible paragraphs in "
              f"{len(doc['strata'])} strata; digest {doc['digest'][:16]}")
        print(f"appended a2_frozen (audit_ref = manifest digest); wrote {SPLIT_FILE.name}   "
              f"commit both -- the freeze is TERMINAL")
        return
    if args.from_manifest:
        sample = json.loads(Path(args.from_manifest).read_text(encoding="utf-8"))
        write_a2_sheets(sample, rows, assigned, registry)
        return
    if args.report:
        return

    # ---- record new members and their assignment --------------------------- #
    members = registry.members()
    fresh = [(k, v) for k, v in assigned.items() if k not in members]
    if fresh:
        version = f"v{len(registry.corpus_versions) + 1}"
        new_members = [{"article_id": k[0], "paragraph_id": k[1], "digest": k[2],
                        "initial_split": v}
                       for k, v in sorted(fresh, key=lambda kv: kv[0][1])]
        registry.corpus_versions.append({
            "version": version,
            "created": date.today().isoformat(),
            "closed": True,
            "provenance": _provenance(),
            "input_binding": _input_binding(read_manifest()),

            "member_count": len(new_members),
            "checksum": sr.version_checksum(new_members),
            "members": new_members,
            "derivation": ("Additive corpus growth: identities absent from every earlier "
                           "version, routed by split_registry.route(). Quarantined identities "
                           "are included as full members with QUARANTINE_ADJUDICATE as their "
                           "initial_split; they are held back from the reserve, not from the "
                           "record."),
        })
        added = registry.append_assignments(
            fresh, reason=f"additive corpus growth, {version}",
            audit_ref=f"build_sheets-{date.today().isoformat()}",
            reasons=quarantined)
        print(f"\nrecorded corpus version {version}: {len(new_members)} new identities, "
              f"{added} assignment events "
              f"({sum(1 for k, v in fresh if v == sr.QUARANTINE)} quarantined)")

    # No mutable quarantine array any more. A quarantine is an assignment event
    # with its reason in the log, so it is append-only and covered by P4's
    # disappearance check like every other member.
    registry.data.pop("quarantine", None)

    # ---- write the authoritative record FIRST ------------------------------ #
    # ALL_paragraphs.csv used to be written before this. A save() that then failed
    # its validation or prefix check would have left the reference sheet
    # describing a state the registry never accepted -- a derived artifact ahead
    # of its source. The registry is the authority, so it lands first and the
    # derived files are only written once it has been accepted.
    registry.data["articles_with_prose"] = with_prose
    registry.data["articles_without_prose"] = [
        {"id": a, "outlet": o, "status": s} for a, o, s in skipped]
    registry.data["counts"] = {
        "by_split": dict(Counter(assigned.values())),
        "quarantined": len(quarantined),
        "collected": len(rows),
        "candidates": len([r for r in rows if r["_candidate"]]),
        "gap_marked_candidates": len([r for r in rows
                                      if r["_candidate"] and r["has_transcription_gap"]]),
        "note": ("Derived from the assignment log at write time, not hand-maintained. "
                 "Every collected paragraph receives exactly one split, quarantine "
                 "included, and that is asserted before writing."),
    }
    # Appended before the save, and independent of whether any identity was fresh.
    # Recording the acceptance on the new version block meant metadata-only drift
    # -- drift with nothing new to add -- was accepted silently and re-appeared,
    # unacknowledged, on every later run.
    # (drift acceptance, if any, was recorded and saved above, before every branch)

    registry.save(SPLIT_FILE)
    print(f"\nwrote {SPLIT_FILE.name}   commit this — it is the disjointness record")

    OUT.mkdir(exist_ok=True)
    # ---- P0: never write a sheet for a closed split ------------------------ #
    # This loop used to write both blades for A0 and A1 unconditionally. A run
    # after the corpus grew would have replaced the blank templates that the
    # coded files -- and therefore the six committed label files -- were derived
    # from, with templates for a LARGER set. No label row would have been lost;
    # the provenance chain from a label file back to its source sheet would have
    # been. That is evidence destruction, so it is the first guard.
    closed = registry.closed_split_names()
    for split, name in (("A0", "A0_calibration"), ("A1", "A1_development")):
        if split in closed:
            print(f"\n  SKIPPED sheets/{name}_sheet-1.csv and _sheet-2.csv — {split} is closed; "
                  f"its sheets are the provenance of finalized labels")
            continue
        sub = [r for r in rows if assigned.get(
            (r["article_id"], r["paragraph_id"], r["digest_full"])) == split]
        with (OUT / f"{name}_sheet-1.csv").open("w", newline="", encoding=ENC) as f:
            w = csv.DictWriter(f, fieldnames=SHEET1_COLUMNS, delimiter=DELIM)
            w.writeheader()
            for r in sorted(sub, key=lambda x: x["paragraph_id"]):
                w.writerow(blank_row(r))
        for r in sub:
            r["split"] = split
        mpath = write_session_manifest(split, sub, registry, kind="sheet-1")
        print(f"wrote labels/{mpath.name}   session manifest, IDs and hashes only")
        with (OUT / f"{name}_sheet-2.csv").open("w", newline="", encoding=ENC) as f:
            w2 = csv.writer(f, delimiter=DELIM)
            w2.writerow(SHEET2_COLUMNS)
            w2.writerow([SHEET2_LABELS.get(c, "") for c in SHEET2_COLUMNS])   # filled by expand_sheet2.py
        print(f"\nwrote sheets/{name}_sheet-1.csv   {len(sub)} rows to code")
        print(f"wrote sheets/{name}_sheet-2.csv   empty — fill after pass 1")

    with (OUT / "ALL_paragraphs.csv").open("w", newline="", encoding=ENC) as f:   # noqa: E501
        w = csv.writer(f, delimiter=DELIM)
        w.writerow(["paragraph_id", "split", "article_id", "text_status", "lang",
                    "outlet", "country", "pub_date", "n_words", "text_sha256",
                    "has_transcription_gap", "headline", "paragraph_text"])
        for r in sorted(rows, key=lambda x: x["paragraph_id"]):
            k = (r["article_id"], r["paragraph_id"], r["digest_full"])
            w.writerow([r["paragraph_id"], assigned.get(k, sr.QUARANTINE), r["article_id"],
                        r["_status"], r["lang"], r["outlet"], r["country"], r["pub_date"],
                        len(r["paragraph_text"].split()), r["text_sha256"],
                        r["has_transcription_gap"], r["headline"], r["paragraph_text"]])
    print(f"wrote sheets/ALL_paragraphs.csv   {len(rows)} rows, reference only")




if __name__ == "__main__":
    main()
