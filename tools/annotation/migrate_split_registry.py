#!/usr/bin/env python3
"""One-time migration: legacy split-assignment.json (schema 1) -> registry (schema 2).

EVERY CHECK RUNS BEFORE ANYTHING IS WRITTEN, and a single failure writes nothing.
This is the one irreversible-feeling step in the whole integration: full digests
and identity triples have to be derived from a legacy file that stored only
`sha256(text)[:16]`, and a mistaken join here would freeze the wrong assignment
into the record the thesis cites. It is not actually irreversible -- the
pre-migration file is git-tracked and its checksum is copied into the new record
-- but it is the step with the least forgiving failure mode, so it is gated.

    python tools/annotation/migrate_split_registry.py            # audit only
    python tools/annotation/migrate_split_registry.py --write    # audit, then write

The audit gate, from plan step P6:

  * one-to-one match for all 198 legacy entries
  * exact counts A0 20 / A0b 12 / A1 32 / RESERVED_A2 134
  * zero unmatched legacy keys
  * zero ambiguous truncated-prefix matches
  * the pre-migration file's checksum recorded in the new block
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_sheets as bs                                    # noqa: E402
import split_registry as sr                                  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CORPUS = ROOT / "corpus"

# What v1 must contain. Not derived from the file being migrated -- asserted
# against it, so a legacy file that had already drifted cannot define its own
# expectation. These are the counts the finalized label files describe.
EXPECTED_V1 = {"A0": 20, "A0b": 12, "A1": 32, "RESERVED_A2": 134}

# The manifest v1 was actually built from: 35 articles, before batch 3. HEAD
# already carries 77, so hashing the current file cannot represent v1's input.
V1_MANIFEST_COMMIT = "891110d"
V1_MANIFEST_BLOB = "82ca681f72d12f7051a6deb1edc583b21186fb18"

ALGORITHM_VERSION = "1"          # segment() + matched_tech() as of this commit

# The exact bytes this migration is written for. Verified equal to the committed
# artifact at HEAD, so a legacy file that had been edited -- swapping two split
# assignments while keeping 198 entries and the same 20/12/32/134 counts, which
# every other check would wave through -- cannot be migrated by accident.
LEGACY_SHA256 = "77723ccfb414fb601bb59da8206bc1d711b4e6d8fccde86222d5327db83756a0"


# Only the code that can change a paragraph's identity, its candidate status or
# its routing is bound. Binding every .py in the directory meant editing the test
# suite tripped the drift guard and blocked the build — a check that fires on
# changes it has no reason to care about gets switched off, so it is scoped to
# what actually matters: segmentation and keyword matching live in build_sheets,
# containment and routing in split_registry.
IDENTITY_CODE = ("build_sheets.py", "split_registry.py")


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def code_provenance() -> dict:
    """The revision the migration was RUN FROM, plus hashes of the code that ran.

    Deliberately not called `producing_commit`: an artifact cannot contain the SHA
    of the commit that first contains it. And because `corpus`
    is entirely gitignored, no commit can bind the corpus either -- that is what
    the per-file hashes below are for. So the clean-tree requirement is scoped to
    the CODE that computes identities; corpus state is bound by content instead.
    """
    code_files = [HERE / n for n in IDENTITY_CODE if (HERE / n).exists()]
    dirty = [l for l in git("status", "--porcelain", "--", "tools/annotation").splitlines() if l]
    return {
        "code_input_commit": git("rev-parse", "HEAD") or "unknown",
        "code_input_commit_short": git("rev-parse", "--short", "HEAD") or "unknown",
        "code_tree_dirty_for_tools_annotation": dirty,
        "code_file_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:16]
                             for p in code_files},
        "python": platform.python_version(),
        "runtime": sr.runtime_fingerprint(),
        "algorithm_version": ALGORITHM_VERSION,
        "min_words": bs.MIN_WORDS,
        "seed": bs.SEED,
    }


def input_binding(article_ids: set[str], manifest: dict, *, historical: bool) -> dict:
    """Bind a corpus version to its inputs by content.

    For v1 the manifest is identified by its historical git blob, which IS
    verifiable, while the article files it referenced are gitignored and can only
    be bound by their present content. That is a real limit and it is stated: the
    hashes establish that today's files still produce v1's identities -- which P4
    checks on every run -- not that a lost file could be reconstructed.
    """
    files = {}
    for aid in sorted(article_ids):
        rel = manifest.get(aid, {}).get("file", "")
        p = CORPUS / rel
        if rel and p.exists():
            files[rel] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    out = {
        "referenced_files": len(files),
        "file_sha256": files,
        "binding_note": ("Article files are gitignored, so they are bound by content, not by "
                         "commit. These hashes establish that the present files still yield "
                         "this version's identities; they do not make a lost file "
                         "reconstructable. Reproducible archival is a separate prerequisite."),
    }
    if historical:
        out["manifest_git_blob"] = V1_MANIFEST_BLOB
        out["manifest_git_commit"] = V1_MANIFEST_COMMIT
        out["manifest_articles_at_that_commit"] = 35
        out["manifest_note"] = ("v1 was built from the 35-article manifest at this blob. HEAD "
                                "carries 77 articles, so the current manifest hash cannot "
                                "represent v1's input state.")
    else:
        cur = CORPUS / "manifest.csv"
        out["manifest_sha256"] = hashlib.sha256(cur.read_bytes()).hexdigest()[:16]
        out["manifest_articles"] = len(manifest)
    out["article_meta_sha256"] = {aid: bs._article_meta_hash(manifest[aid])
                                  for aid in sorted(article_ids) if aid in manifest}
    out["count_bearing_fields"] = list(bs.COUNT_BEARING_FIELDS)
    return out


def audit(allow_dirty: bool = False) -> tuple[list[str], dict]:
    """Return (problems, findings). Writes nothing, ever."""
    problems: list[str] = []

    # The clean-tree gate. Earlier this was documented in the plan and recorded in
    # the output, but never enforced -- the record itself listed modified files
    # and the migration ran anyway. It is scoped to the code that COMPUTES
    # identities, because `corpus` is entirely gitignored and
    # no commit can bind the corpus; corpus state is bound by content instead.
    prov = code_provenance()
    dirty = prov["code_tree_dirty_for_tools_annotation"]
    if prov["code_input_commit"] == "unknown":
        problems.append("git provenance is unavailable, so the code that computed these "
                        "identities cannot be named. Refusing.")
    if dirty and not allow_dirty:
        problems.append(
            f"{len(dirty)} uncommitted change(s) under tools/annotation:\n"
            + "\n".join(f"        {d}" for d in dirty[:10])
            + "\n     The migration records which code computed these identities. Commit the "
              "implementation first, or pass --allow-dirty to record the deviation explicitly "
              "in the version block (the code file hashes are recorded either way).")
    legacy_path = sr.REGISTRY_FILE
    if not legacy_path.exists():
        return [f"no file at {legacy_path}"], {}

    raw_bytes = legacy_path.read_bytes()
    actual = hashlib.sha256(raw_bytes).hexdigest()
    if actual != LEGACY_SHA256 and json.loads(raw_bytes.decode("utf-8")).get(
            "schema_version") != sr.SCHEMA_VERSION:
        problems.append(
            f"the file to migrate hashes to {actual[:16]}…, but this migration is written for "
            f"{LEGACY_SHA256[:16]}… (the artifact committed at HEAD). Its contents differ from "
            f"the record the expected counts were taken from, so the counts are no longer "
            f"evidence of anything. Refusing.")
    legacy = json.loads(raw_bytes.decode("utf-8"))
    if legacy.get("schema_version") == sr.SCHEMA_VERSION:
        return ["already migrated: the file is schema_version 2. Nothing to do."], {}

    by_sha: dict[str, str] = legacy.get("by_text_sha256", {})
    if not by_sha:
        return ["the legacy file has no by_text_sha256 map; nothing to migrate from."], {}

    rows, _skipped = bs.collect()
    for r in rows:
        r["digest_full"] = sr.digest(r["paragraph_text"])
        r["digest_trunc"] = r["digest_full"][:16]

    # ---- match legacy truncated hashes onto current paragraphs ------------- #
    by_trunc: dict[str, list[dict]] = {}
    for r in rows:
        by_trunc.setdefault(r["digest_trunc"], []).append(r)

    matched: dict[str, dict] = {}
    unmatched: list[str] = []
    ambiguous: list[tuple[str, list[str]]] = []
    for trunc, split in by_sha.items():
        hits = by_trunc.get(trunc, [])
        if not hits:
            unmatched.append(trunc)
        elif len(hits) > 1:
            ambiguous.append((trunc, [h["paragraph_id"] for h in hits]))
        else:
            matched[trunc] = hits[0]

    if len(by_sha) != 198:
        problems.append(f"the legacy map has {len(by_sha)} entries, expected 198. This "
                        f"migration is written for the pre-batch-3 record.")
    if unmatched:
        problems.append(
            f"{len(unmatched)} legacy hash(es) do not match any current paragraph: "
            f"{unmatched[:6]}{' ...' if len(unmatched) > 6 else ''}. A recorded paragraph's "
            f"text changed. Restore it before migrating -- migrating now would drop a coded "
            f"paragraph out of the record entirely.")
    if ambiguous:
        problems.append(
            f"{len(ambiguous)} legacy hash(es) match more than one current paragraph: "
            f"{ambiguous[:4]}. A 16-hex prefix collided, so the join is not one-to-one and "
            f"the full digest cannot be inferred safely.")
    if len(matched) != len(by_sha) - len(unmatched) - len(ambiguous):
        problems.append("internal inconsistency in the match tally; refusing.")

    got = Counter(by_sha[t] for t in matched)
    if dict(got) != EXPECTED_V1:
        problems.append(f"matched split counts {dict(sorted(got.items()))} do not equal the "
                        f"expected {dict(sorted(EXPECTED_V1.items()))}. The label files "
                        f"describe those counts; a mismatch means the record drifted.")

    # ---- identity triples must be unique ----------------------------------- #
    triples = [(r["article_id"], r["paragraph_id"], r["digest_full"]) for r in matched.values()]
    dupes = [k for k, n in Counter(triples).items() if n > 1]
    if dupes:
        problems.append(f"{len(dupes)} duplicate identity triple(s) among matched members: "
                        f"{[d[1] for d in dupes[:5]]}.")

    findings = {
        "legacy_file_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "legacy_raw_text": raw_bytes.decode("utf-8"),
        "legacy_entries": len(by_sha),
        "matched": len(matched),
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "counts": dict(sorted(got.items())),
        "expected": EXPECTED_V1,
        "collected_paragraphs": len(rows),
        "rows": rows,
        "matched_rows": matched,
        "by_sha": by_sha,
        "legacy": legacy,
    }
    return problems, findings


def build_registry(f: dict) -> dict:
    legacy = f["legacy"]
    manifest = bs.read_manifest()
    members, log_pairs = [], []
    for trunc, r in sorted(f["matched_rows"].items(), key=lambda kv: kv[1]["paragraph_id"]):
        split = f["by_sha"][trunc]
        # initial_split is the anchor the assignment log is replayed against, and
        # the version checksum covers it. Without it, a rewritten first assignment
        # could move a coded paragraph into the reserve and pass every check.
        members.append({"article_id": r["article_id"], "paragraph_id": r["paragraph_id"],
                        "digest": r["digest_full"], "initial_split": split})
        log_pairs.append(((r["article_id"], r["paragraph_id"], r["digest_full"]), split))

    v1_articles = {m["article_id"] for m in members}
    v1 = {
        "version": "v1",
        "created": legacy.get("created", "2026-08-29"),
        "closed": True,
        "provenance": code_provenance(),
        "input_binding": input_binding(v1_articles, manifest, historical=True),
        "legacy_source_sha256": f["legacy_file_sha256"],
        "member_count": len(members),
        "checksum": sr.version_checksum(members),
        "members": members,
        "derivation": ("Migrated from the legacy by_text_sha256 map by matching its 16-hex "
                       "truncated hashes one-to-one onto full SHA-256 digests recomputed from "
                       "the corpus. The audit gate in migrate_split_registry.py required a "
                       "one-to-one join, the exact expected counts, and no prefix collisions."),
    }

    data = {
        "schema_version": sr.SCHEMA_VERSION,
        "note": ("Corpus membership and split assignment are separate records. "
                 "`corpus_versions` holds immutable membership deltas keyed by the identity "
                 "triple (article_id, paragraph_id, sha256). `assignment_log` is an append-only, "
                 "checksum-chained log of assignment events; an identity's current split is the "
                 "last event naming it. A0, A0b, A1 and A1b are terminal. The only permitted "
                 "transition is an audited RESERVED_A2 -> A1b, and only before the terminal "
                 "a2_frozen event. RESERVED_A2 paragraphs must not be shown to the annotator "
                 "or used to tune the prompt or the codebook. "
                 "The legacy by_article / by_text_sha256 fields are preserved in "
                 "`legacy_snapshot` for evidence and are NOT read by assignment code."),
        "codebook_version": legacy.get("codebook_version", bs.CODEBOOK_VERSION),
        "corpus_versions": [v1],
        "assignment_log": [],
        "quarantine": [],
        "legacy_snapshot": {
            # The complete pre-migration file, verbatim, plus its checksum -- which
            # load() verifies. An earlier version copied selected FIELDS out of the
            # legacy object, which silently dropped `codebook_version`, lost the
            # original serialization, and recorded a checksum nobody ever checked.
            # Reconstruction was then only possible via git. The raw text is 11 KB
            # and makes the migration reversible from this file alone.
            "checksum_sha256": f["legacy_file_sha256"],
            "schema_version": 1,
            "captured": "2026-09-02, by migrate_split_registry.py",
            "read_by_assignment_code": False,
            "reconstruct": ("json.loads(legacy_snapshot['raw_json']) reproduces the "
                            "pre-migration object exactly; the raw text reproduces the file "
                            "byte for byte."),
            "raw_json": f["legacy_raw_text"],
        },
    }
    reg = sr.SplitRegistry(data)
    reg.append_assignments(log_pairs, reason="migrated from legacy by_text_sha256",
                           audit_ref="migration-2026-09-02")
    return reg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write after a clean audit")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="record an uncommitted implementation as an explicit deviation")
    args = ap.parse_args()

    problems, f = audit(allow_dirty=args.allow_dirty)
    print("═══ MIGRATION AUDIT ═══\n")
    if f:
        print(f"  legacy entries        {f['legacy_entries']}")
        print(f"  matched one-to-one    {f['matched']}")
        print(f"  unmatched             {len(f['unmatched'])}")
        print(f"  ambiguous prefixes    {len(f['ambiguous'])}")
        print(f"  counts                {f['counts']}")
        print(f"  expected              {dict(sorted(f['expected'].items()))}")
        print(f"  corpus paragraphs     {f['collected_paragraphs']}")
        print(f"  legacy file sha256    {f['legacy_file_sha256'][:32]}…\n")

    if problems:
        print(f"REFUSING to migrate -- {len(problems)} problem(s):\n", file=sys.stderr)
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}\n", file=sys.stderr)
        print("Nothing was written.", file=sys.stderr)
        sys.exit(1)

    print("  GATE PASSED — every audit condition holds.")
    if args.allow_dirty:
        d = code_provenance()["code_tree_dirty_for_tools_annotation"]
        print(f"  DEVIATION: --allow-dirty, {len(d)} uncommitted change(s) under "
              f"tools/annotation. Recorded in the version block with per-file code hashes.")
    print()
    if not args.write:
        print("Dry run. Pass --write to migrate.")
        return

    reg = build_registry(f)
    reg.save()
    print(f"wrote {sr.REGISTRY_FILE.name}  schema_version {sr.SCHEMA_VERSION}")
    print(f"  corpus version v1     {len(reg.corpus_versions[0]['members'])} members, "
          f"checksum {reg.corpus_versions[0]['checksum']}")
    print(f"  assignment log        {len(reg.assignment_log)} entries, chain intact")
    print(f"  splits                {dict(Counter(reg.current_splits().values()))}")
    print("\nCommit this — it is the disjointness record.")


if __name__ == "__main__":
    main()
