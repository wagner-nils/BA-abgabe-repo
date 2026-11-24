#!/usr/bin/env python3
"""Contract tests for the split registry. Plan step P9.

Every test runs against an INJECTED fixture -- a temporary registry and synthetic
paragraphs -- never the live record. An earlier draft of this suite would have run
the live builder, which writes into `sheets/`; that would have made the test able
to destroy the evidence it exists to protect.

The assertion list is not decoration. Each one corresponds to a way already-seen
text could reach the evaluation set, or a way the reported denominator could move,
and several of them exist because a review round found the earlier design
admitting exactly that case.

    python tools/annotation/test_split_registry.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import split_registry as sr                                  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"\n          {detail}" if not cond and detail else ""))


def expect_raises(name: str, fn, needle: str = "") -> None:
    try:
        fn()
    except sr.RegistryError as e:
        if needle and needle.lower() not in str(e).lower():
            check(name, False, f"raised, but not about {needle!r}: {e}")
        else:
            check(name, True)
        return
    check(name, False, "did not raise RegistryError")


# ─── fixture ─────────────────────────────────────────────────────────────────
def para(article: str, n: int, text: str) -> dict:
    return {"article_id": article, "paragraph_id": f"{article}_p{n:02d}",
            "paragraph_text": text, "digest_full": sr.digest(text)}


def filler(i: int, extra: str = "") -> str:
    """A 24-token paragraph sharing NO 8-word shingle with any other filler.

    The first draft of this helper built every paragraph from one shared word list
    with a rotating offset, which made neighbouring fillers genuine near-duplicates
    — and P9.3 failed because the containment check correctly quarantined one. That
    is worth recording rather than hiding: the check is more sensitive than the
    fixture was careful. Tokens are now unique per paragraph by construction, so a
    quarantine in this suite always means the code decided it, never the data.
    """
    body = " ".join(f"t{i}w{j}" for j in range(24))
    return f"{body} {extra}".strip()


def build_fixture(tmp: Path) -> tuple[sr.SplitRegistry, list[dict], dict]:
    """A0 article, A1 article, mixed A0b/reserve article, pure reserve article."""
    rows = []
    rows += [para("A900", i, filler(100 + i)) for i in (1, 2)]          # A0, whole article
    rows += [para("A901", i, filler(200 + i)) for i in (1, 2)]          # A1, whole article
    rows += [para("A902", i, filler(300 + i)) for i in (1, 2, 3)]       # mixed: p01 A0b, rest reserve
    rows += [para("A903", i, filler(400 + i)) for i in (1, 2)]          # pure reserve

    assign = {
        "A900_p01": "A0", "A900_p02": "A0",
        "A901_p01": "A1", "A901_p02": "A1",
        "A902_p01": "A0b", "A902_p02": sr.RESERVE, "A902_p03": sr.RESERVE,
        "A903_p01": sr.RESERVE, "A903_p02": sr.RESERVE,
    }
    members = [{"article_id": r["article_id"], "paragraph_id": r["paragraph_id"],
                "digest": r["digest_full"], "initial_split": assign[r["paragraph_id"]]}
               for r in rows]
    data = {
        "schema_version": sr.SCHEMA_VERSION,
        "corpus_versions": [{
            "version": "v1", "closed": True, "member_count": len(members),
            # Computed, not omitted, and it covers initial_split — that is what
            # anchors the log. An earlier draft omitted the checksum entirely and
            # was correctly refused by check_versions().
            "checksum": sr.version_checksum(members),
            # A file binding is REQUIRED — check_versions() refuses a version
            # without one, because removing it used to pass every guard while
            # destroying the per-version article-content evidence.
            "input_binding": {"referenced_files": 4,
                              "file_sha256": {f"{a}.md": sr.digest(a)[:16]
                                              for a in ("A900", "A901", "A902", "A903")}},
            "provenance": {"runtime": sr.runtime_fingerprint(),
                           "code_file_sha256": {}},
            "members": members}],
        "assignment_log": [],
    }
    reg = sr.SplitRegistry(data)
    reg.append_assignments([((r["article_id"], r["paragraph_id"], r["digest_full"]),
                             assign[r["paragraph_id"]]) for r in rows],
                           reason="fixture", audit_ref="test")
    return reg, rows, assign


def clone(reg: sr.SplitRegistry) -> sr.SplitRegistry:
    return sr.SplitRegistry(json.loads(json.dumps(reg.data)))


# ─── the assertions ──────────────────────────────────────────────────────────
def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="split-registry-test-"))
    reg, rows, _ = build_fixture(tmp)

    print("\nfixture: A900 (A0) · A901 (A1) · A902 (A0b p01 + reserve p02-p03) · A903 (reserve)")
    print(f"closed articles: {sorted(reg.closed_article_ids())}   "
          f"mixed: {sorted(reg.mixed_article_ids())}\n")

    check("F1  closed_article_ids is exactly the whole-membership-terminal articles",
          reg.closed_article_ids() == {"A900", "A901"},
          f"got {sorted(reg.closed_article_ids())}")
    check("F2  A902 is mixed, not closed — its reserve paragraphs stay extendable",
          reg.mixed_article_ids() == {"A902"} and "A902" not in reg.closed_article_ids(),
          f"mixed={sorted(reg.mixed_article_ids())}")

    # --- P9.1 a clean build is a no-op ------------------------------------- #
    base = sr.route(reg, rows)
    check("P9.1  clean routing reproduces every recorded split unchanged",
          all(base["assigned"][(r["article_id"], r["paragraph_id"], r["digest_full"])]
              == reg.current_splits()[(r["article_id"], r["paragraph_id"], r["digest_full"])]
              for r in rows) and not base["quarantined"])

    # --- P9.2 added paragraph in a closed article aborts -------------------- #
    expect_raises("P9.2  added paragraph in a CLOSED article aborts",
                  lambda: sr.route(reg, rows + [para("A901", 3, filler(777))]),
                  "terminal split")

    # --- P9.3 added paragraph in a pure reserve article -> RESERVED_A2 ------ #
    new_pure = para("A903", 3, filler(888))
    r3 = sr.route(reg, rows + [new_pure])
    key3 = (new_pure["article_id"], new_pure["paragraph_id"], new_pure["digest_full"])
    check("P9.3  added paragraph in a PURE reserve article lands in RESERVED_A2",
          r3["assigned"].get(key3) == sr.RESERVE,
          f"got {r3['assigned'].get(key3)} / quarantined={key3 in r3['quarantined']}")

    # --- P9.4 added paragraph in a mixed article -> quarantine -------------- #
    new_mixed = para("A902", 4, filler(999))
    r4 = sr.route(reg, rows + [new_mixed])
    key4 = (new_mixed["article_id"], new_mixed["paragraph_id"], new_mixed["digest_full"])
    check("P9.4  added paragraph in a MIXED A0b/reserve article is QUARANTINED",
          key4 in r4["quarantined"] and r4["assigned"].get(key4) == sr.QUARANTINE,
          f"assigned={r4['assigned'].get(key4)}")

    # --- P9.5 exact re-insertion elsewhere -> quarantine -------------------- #
    recorded = next(r for r in rows if r["paragraph_id"] == "A900_p01")
    reinsert = para("A903", 4, recorded["paragraph_text"])
    r5 = sr.route(reg, rows + [reinsert])
    key5 = (reinsert["article_id"], reinsert["paragraph_id"], reinsert["digest_full"])
    check("P9.5  EXACT re-insertion of a recorded paragraph elsewhere is QUARANTINED",
          key5 in r5["quarantined"] and r5["assigned"].get(key5) == sr.QUARANTINE,
          f"assigned={r5['assigned'].get(key5)}")

    # --- P9.6 near-duplicate -> quarantine (the A010_p15 bypass) ----------- #
    near = para("A903", 5, recorded["paragraph_text"] + " Ein zusaetzlicher Satz kommt hinzu.")
    r6 = sr.route(reg, rows + [near])
    key6 = (near["article_id"], near["paragraph_id"], near["digest_full"])
    hit = next((c for pid, _, c in r6["containment_hits"] if pid == near["paragraph_id"]), None)
    check("P9.6  NEAR-DUPLICATE insertion is QUARANTINED (the verified bypass)",
          key6 in r6["quarantined"] and r6["assigned"].get(key6) == sr.QUARANTINE,
          f"assigned={r6['assigned'].get(key6)}  containment={hit}")
    check("P9.6b the bypass scores containment 1.0 against the recorded paragraph",
          hit is not None and hit >= 0.99, f"containment={hit}")

    # --- P9.7 mutated historical paragraph aborts -------------------------- #
    mutated = [r for r in rows if r["paragraph_id"] != "A900_p01"]
    mutated.append(para("A900", 1, filler(100 + 1, "MUTATED")))
    expect_raises("P9.7  a MUTATED recorded paragraph aborts (identity disappeared)",
                  lambda: sr.verify_closed_versions(reg, mutated),
                  "no longer resolve")

    # --- P9.8 RESERVED_A2 -> A1b permitted before freeze, refused after ----- #
    g = clone(reg)
    res_key = next(k for k, s in g.current_splits().items() if s == sr.RESERVE)
    g.append_assignments([(res_key, "A1b")], reason="A1b withdrawal", audit_ref="test-a1b")
    check("P9.8  RESERVED_A2 -> A1b is permitted BEFORE the freeze",
          g.current_splits()[res_key] == "A1b")

    g2 = clone(reg)
    g2.freeze_a2(sr.eligible_checksum_of(g2.current_splits()), audit_ref="test-freeze")
    res_key2 = next(k for k, s in g2.current_splits().items() if s == sr.RESERVE)
    expect_raises("P9.8b RESERVED_A2 -> A1b is REFUSED after the freeze",
                  lambda: g2.append_assignments([(res_key2, "A1b")], "late", "test"),
                  "frozen")

    g2b = clone(reg)
    expect_raises("P9.8c a freeze with an UNVERIFIED checksum is REFUSED",
                  lambda: g2b.freeze_a2("deadbeef", audit_ref="test"), "does not match")

    # --- P9.9 transitions out of terminal splits refused ------------------- #
    for term in ("A0", "A0b", "A1", "A1b"):
        gg = clone(reg)
        if term == "A1b":
            k = next(k for k, s in gg.current_splits().items() if s == sr.RESERVE)
            gg.append_assignments([(k, "A1b")], "setup", "test")
        else:
            k = next(k for k, s in gg.current_splits().items() if s == term)
        expect_raises(f"P9.9  {term} -> RESERVED_A2 is REFUSED (terminal)",
                      lambda gg=gg, k=k: gg.append_assignments([(k, sr.RESERVE)], "x", "test"),
                      "terminal")

    # --- P9.9a a tampered log is refused by the chain ---------------------- #
    # The first version of this loop "edited" the first entry by setting its split
    # to A0 — which it already was. The mutation was a no-op, so the assertion
    # passed on the missing link of a stale list rather than on a detected edit.
    # Every mangle below is now a genuine change, and each is checked against the
    # guard that is actually supposed to catch it.
    for label, mangle, needle in (
        ("edited-split", lambda log: [{**log[0], "split": sr.RESERVE}] + log[1:], "link"),
        ("edited-digest", lambda log: [{**log[0], "digest": "0" * 64}] + log[1:], "link"),
        ("reordered", lambda log: [log[1], log[0]] + log[2:], "link"),
    ):
        d = json.loads(json.dumps(reg.data))
        before = d["assignment_log"][0]
        d["assignment_log"] = mangle(d["assignment_log"])
        check(f"P9.9a the {label} mangle actually changes the entry",
              d["assignment_log"][0] != before, "the mutation was a no-op")
        problems = sr.verify_chain(d["assignment_log"])
        check(f"P9.9a an {label.upper()} log is refused by the chain check", bool(problems),
              "chain verified clean, which it must not")

    # A re-chained tamper defeats the chain by construction — there is no secret —
    # so the anchor and the replay are what must catch it.
    d = json.loads(json.dumps(reg.data))
    d["assignment_log"][0]["split"] = sr.RESERVE
    d["assignment_log"] = sr.chain(d["assignment_log"])
    check("P9.9c a RE-CHAINED initial assignment (A0 -> reserve) is caught by the anchor",
          bool(sr.verify_chain(d["assignment_log"])) is False
          and bool(sr.validate_log(d["assignment_log"], set(reg.members()),
                                   reg.initial_splits())),
          "chain clean AND replay clean — seen text would reach the reserve")

    d2 = json.loads(json.dumps(reg.data))
    d2["assignment_log"] = d2["assignment_log"][:-1]
    d2["assignment_log"] = sr.chain(d2["assignment_log"])
    check("P9.9d a re-chained TRUNCATED log is caught by replay (member unassigned)",
          bool(sr.validate_log(d2["assignment_log"], set(reg.members()),
                               reg.initial_splits())))

    live = tmp / "reg.json"
    reg.save(live)
    short = clone(reg)
    short.assignment_log = short.assignment_log[:-1]
    # Truncating now trips validate_log first — the dropped entries leave recorded
    # members with no assignment — and the prefix rule second. Either refusal is
    # correct; asserting on the message would pin the wrong guard.
    expect_raises("P9.9a a SHORTENED log is refused on save", lambda: short.save(live))
    edited = clone(reg)
    edited.assignment_log = sr.chain([{**edited.assignment_log[0], "reason": "tampered"}]
                                     + edited.assignment_log[1:])
    expect_raises("P9.9a an EDITED stored prefix is refused on save",
                  lambda: edited.save(live), "prefix")

    # --- P9.9b no assignment event after the freeze ------------------------ #
    g3 = clone(reg)
    # A real, recomputed checksum. The first version of this test froze with the
    # literal "deadbeef" and it was accepted — exactly the defect the verified
    # freeze now closes, so the test had to stop relying on it.
    g3.freeze_a2(sr.eligible_checksum_of(g3.current_splits()), audit_ref="test")
    newp = para("A903", 6, filler(1234))
    expect_raises("P9.9b any assignment event after a2_frozen is REFUSED",
                  lambda: g3.append_assignments(
                      [((newp["article_id"], newp["paragraph_id"], newp["digest_full"]),
                        sr.RESERVE)], "late growth", "test"),
                  "frozen")

    d3 = json.loads(json.dumps(reg.data))
    seqmax = max(e["seq"] for e in d3["assignment_log"])
    d3["assignment_log"].append({"seq": seqmax + 1, "kind": sr.EVENT_FREEZE,
                                 "eligible_checksum": "deadbeef", "audit_ref": "forged"})
    d3["assignment_log"] = sr.chain(d3["assignment_log"])
    check("P9.8d a re-chained FORGED freeze checksum is caught by replay",
          any("hashes to" in x for x in sr.validate_log(
              d3["assignment_log"], set(reg.members()), reg.initial_splits())))

    # --- drift acceptance is keyed to observed state, not to wording -------- #
    # Accepting "A004's language changed" once must NOT accept it changing again.
    # The earlier version stored the note text, and `en` then `fr` read identically.
    d4 = clone(reg)
    first = {"version": "v1", "component": "article_meta", "key": "A004",
             "recorded": "aaaa", "observed": "bbbb"}
    second = {**first, "observed": "cccc"}
    check("D1  two different observations have different fingerprints",
          sr.drift_fingerprint(first) != sr.drift_fingerprint(second))
    check("D1b the same observation has a stable fingerprint",
          sr.drift_fingerprint(first) == sr.drift_fingerprint(dict(first)))
    d4.append_drift_acceptance([first], audit_ref="test-accept-1")
    acc = d4.accepted_drift()
    check("D2  the accepted observation is on record",
          sr.drift_fingerprint(first) in acc)
    check("D2b a SECOND change to the same key is NOT covered by the first acceptance",
          sr.drift_fingerprint(second) not in acc,
          "the second change would be silently accepted")
    check("D3  the drift event carries readable notes alongside the fingerprints",
          bool(d4.assignment_log[-1].get("notes"))
          and "A004" in d4.assignment_log[-1]["notes"][0])
    # The duplicate-record / orphan-fingerprint attack: records [A, A] beside
    # fingerprints [fp(A), fp(B)] passed a per-record membership check with equal
    # lengths, authorizing state B with nothing recording it.
    forged = sr.chain([*d4.assignment_log[:-1],
                       {**d4.assignment_log[-1],
                        "records": [first, first],
                        "fingerprints": sorted([sr.drift_fingerprint(first),
                                                sr.drift_fingerprint(second)]),
                        "notes": sorted([sr.format_drift(first), sr.format_drift(first)])}])
    probs = sr.validate_log(forged, set(d4.members()), d4.initial_splits())
    check("D5  a DUPLICATE record with an ORPHAN fingerprint is refused by replay",
          any("no record" in x or "do not correspond exactly" in x for x in probs),
          f"accepted: {probs[:2]}")
    check("D6  notes that do not derive from the records are refused",
          any("stored notes" in x for x in sr.validate_log(
              sr.chain([*d4.assignment_log[:-1],
                        {**d4.assignment_log[-1], "notes": ["invented"]}]),
              set(d4.members()), d4.initial_splits())))

    check("D4  a drift event with mismatched fingerprints is refused by replay",
          bool(sr.validate_log(
              sr.chain([*d4.assignment_log[:-1],
                        {**d4.assignment_log[-1], "fingerprints": ["0" * 16]}]),
              set(d4.members()), d4.initial_splits())))

    # --- P9.10 assigned + quarantined partition the corpus ----------------- #
    allrows = rows + [new_pure, new_mixed, reinsert, near]
    r10 = sr.route(reg, allrows)
    # Quarantine is a split inside `assigned`, not a second bucket, so the
    # partition is total by construction rather than by two counters agreeing.
    check("P9.10 every collected paragraph receives exactly one split",
          len(r10["assigned"]) == len(allrows),
          f"{len(r10['assigned'])} vs {len(allrows)}")
    check("P9.10b the quarantine reason index matches the quarantine split count",
          sum(1 for s in r10["assigned"].values() if s == sr.QUARANTINE)
          == len(r10["quarantined"]))

    # --- P5 duplicate identity triple is fatal ----------------------------- #
    expect_raises("P5    a duplicate identity TRIPLE is fatal (collection bug)",
                  lambda: sr.route(reg, rows + [dict(rows[0])]),
                  "more than once")

    # --- P3a specification is pinned --------------------------------------- #
    check("P3a   threshold and shingle size are the recorded ones",
          sr.CONTAINMENT_THRESHOLD == 0.15 and sr.SHINGLE_WORDS == 8,
          f"{sr.CONTAINMENT_THRESHOLD} / {sr.SHINGLE_WORDS}")
    check("P3a   a sub-8-word paragraph yields no shingles and is not containment-quarantined",
          sr.shingles("short one here") == set())
    check("P3a   containment is normalised by the RECORDED paragraph, not the new one",
          abs(len(sr.shingles(recorded["paragraph_text"] + " tail words added here now") &
                  sr.shingles(recorded["paragraph_text"]))
              / len(sr.shingles(recorded["paragraph_text"])) - 1.0) < 1e-9)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for n, d in FAILED:
            print(f"  FAILED: {n}  {d}")
        sys.exit(1)


# ─── live checks: the real registry, read-only ───────────────────────────────
# The fixture proves the mechanism; these prove the record. Nothing here writes,
# and every number the final report quotes is asserted rather than transcribed.
def live() -> None:
    import csv
    import importlib.util

    spec = importlib.util.spec_from_file_location("bs", str(Path(__file__).parent / "build_sheets.py"))
    bs = importlib.util.module_from_spec(spec)
    sys.argv = ["bs", "--report"]
    spec.loader.exec_module(bs)

    reg = sr.SplitRegistry.load()                     # raises if the chain is broken
    rows, _ = bs.collect()
    sr.verify_closed_versions(reg, rows)              # raises if an identity vanished
    routed = sr.route(reg, rows)
    assigned, quarantined = routed["assigned"], routed["quarantined"]
    cur = reg.current_splits()
    counts = Counter(assigned.values())
    cand = {(r["article_id"], r["paragraph_id"], r["digest_full"]) for r in rows if r["_candidate"]}
    gaps = {(r["article_id"], r["paragraph_id"], r["digest_full"])
            for r in rows if r["has_transcription_gap"]}
    by_key = {(r["article_id"], r["paragraph_id"], r["digest_full"]): r for r in rows}
    a2 = [k for k, v in assigned.items() if v == sr.RESERVE and k in cand]

    print("\n─── live registry ───")
    check("L1  the assignment log chain is intact", not sr.verify_chain(reg.assignment_log))
    check("L2  schema_version is 2", reg.schema_version == 2)
    check("L3  every recorded identity still resolves in the corpus", True)   # raised above if not
    check("L4  A0 has exactly 20 paragraphs", counts["A0"] == 20, f"got {counts['A0']}")
    check("L5  A0b has exactly 12 paragraphs", counts["A0b"] == 12, f"got {counts['A0b']}")
    check("L6  A1 has exactly 32 paragraphs", counts["A1"] == 32, f"got {counts['A1']}")
    # The reserve GROWS legitimately, so pinning an absolute number here made every
    # corpus ingest break the suite — the same over-strictness that made the first
    # binding guard useless. What must hold is that the routed reserve equals what
    # the registry recorded: growth updates both together, and a silent divergence
    # between the record and a fresh routing fails.
    rec_counts = (reg.data.get("counts") or {}).get("by_split") or {}
    check("L7  the routed reserve equals the count the registry recorded",
          counts[sr.RESERVE] == rec_counts.get(sr.RESERVE),
          f"routed {counts[sr.RESERVE]} vs recorded {rec_counts.get(sr.RESERVE)}")
    check("L7b every split's routed count equals its recorded count",
          {k: v for k, v in counts.items()} == rec_counts,
          f"routed {dict(counts)} vs recorded {rec_counts}")
    n_q = sum(1 for s in assigned.values() if s == sr.QUARANTINE)
    check("L8  every collected paragraph has exactly one split",
          len(assigned) == len(rows), f"{len(assigned)} vs {len(rows)}")
    check("L8b the quarantine split count matches the reason index",
          n_q == len(quarantined), f"{n_q} vs {len(quarantined)}")
    rec_gap = (reg.data.get("counts") or {}).get("gap_marked_candidates")
    check("L9  the gap-marked candidate count equals the recorded one",
          len([k for k in a2 if k in gaps]) + sum(
              1 for k, v in assigned.items() if v != sr.RESERVE and k in cand and k in gaps
          ) == rec_gap,
          f"routed {len([k for k in a2 if k in gaps])} in reserve vs recorded {rec_gap} overall")
    langs = Counter(by_key[k]["lang"] for k in a2 if k not in gaps)
    # A balanced 60/60 draw needs 60 clean candidates per language. Asserted as a
    # FLOOR, not an equality: the pool grows, and the design constraint is that it
    # stays large enough, not that it stays the same size.
    check("L10 both language strata still support a 60-paragraph draw",
          langs["de"] >= 60 and langs["en"] >= 60,
          f"clean candidates de {langs['de']}, en {langs['en']}")
    check("L10b the reserve's language composition is recorded in the run output",
          langs["de"] > 0 and langs["en"] > 0, f"got {dict(langs)}")
    check("L11 closed articles are exactly the five whole-membership-terminal ones",
          reg.closed_article_ids() == {"A002", "A019", "A004", "A006", "A018"},
          f"got {sorted(reg.closed_article_ids())}")
    check("L12 the nine A0b-touched reserve articles are mixed, not closed",
          reg.mixed_article_ids() == {"A001", "A003", "A005", "A007", "A008",
                                      "A010", "A011", "A012", "A024"},
          f"got {sorted(reg.mixed_article_ids())}")
    # Validated through the state machine, not by counting events. Counting would
    # wrongly fail after a legitimate RESERVED_A2 -> A1b withdrawal, which puts two
    # events on a terminal identity by design.
    anchored = sr.validate_log(reg.assignment_log, set(reg.members()), reg.initial_splits())
    check("L13 the whole log replays cleanly against the state machine AND the anchors",
          not anchored, "; ".join(anchored[:3]))
    check("L13c every member is anchored and nothing else is",
          not reg.check_anchor_coverage(), "; ".join(reg.check_anchor_coverage()[:3]))
    check("L13d the recorded runtime fingerprint matches the running one",
          all((v.get("provenance") or {}).get("runtime") == sr.runtime_fingerprint()
              for v in reg.corpus_versions),
          "a version was computed under a different runtime")
    check("L13b every corpus version's checksum matches its members",
          not reg.check_versions(), "; ".join(reg.check_versions()[:3]))
    # Against the LOG, not the removed top-level array — which would have compared
    # against a default empty list and passed vacuously.
    logged_q = sum(1 for k, v in cur.items() if v == sr.QUARANTINE)
    check("L14 the quarantine recorded in the log reproduces on a fresh route",
          logged_q == n_q, f"log {logged_q} vs routed {n_q}")
    # State-aware since the real freeze of 2026-09-06: before the freeze the reserve
    # must be open and no frame manifest may exist; after it the Step 1.5 handshake
    # (draw_a2.verify_freeze) must hold on the live registry.
    _fm = Path(__file__).parent / "labels" / "A2_frame_manifest.json"
    _frame = json.loads(_fm.read_text(encoding="utf-8")) if _fm.exists() else None
    if _frame is None:
        check("L15 A2 is not frozen yet and no frame manifest exists", reg.a2_frozen() is None)
    else:
        import draw_a2 as _da
        try:
            _da.verify_freeze(_frame, reg)
            check("L15 A2 is frozen and the handshake holds (head = a2_frozen, audit_ref = frame "
                  "digest, predecessor = pre_freeze_head, eligible checksum equal)", True)
        except Exception as exc:  # noqa: BLE001
            check("L15 A2 is frozen and the handshake holds", False, str(exc))
    # The record must be bound to the code that is on disk NOW. Without this, a
    # post-regeneration edit leaves the registry describing state computed by a
    # different implementation, and the only symptom is that the next build
    # refuses -- which is safe but was easy to miss until it happened.
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("bs_l", str(Path(__file__).parent / "build_sheets.py"))
    _bs = _ilu.module_from_spec(_spec)
    sys.argv = ["bs", "--report"]
    _spec.loader.exec_module(_bs)
    _drift = _bs.verify_input_bindings(reg, _bs.read_manifest())
    unaccepted = [r for r in _drift if sr.drift_fingerprint(r) not in reg.accepted_drift()]
    # One documented exception (labels/README.md, "A2 frame manifest"): the real freeze of
    # 2026-09-06 was appended without the acceptance of the three build_sheets.py
    # code-hash records, and nothing can be appended after the terminal event. Those
    # records are tolerated only while their observed hash is the very generator hash the
    # frozen frame manifest discloses -- i.e. the drift is on record, just not as an event.
    # After the terminal freeze no acceptance can be appended at all (Codex W1 round 1, #14,
    # narrowed): the inputs the corpus versions were built from must still carry no
    # unaccepted drift (L15b); the frozen frame must have been produced by the code the
    # pre-freeze archive stores (L15c, historical provenance); and the unaccepted code-hash
    # records must be either the three the freeze buried -- named exactly, see
    # labels/README.md -- or later drift of the same generator, never any other code (L15d).
    _gen = ((_frame or {}).get("generator") or {}).get("sha256", "")
    _code = [r for r in unaccepted if r.get("component") == "code"]
    _inputs = [r for r in unaccepted if r.get("component") != "code"]
    if _frame is None:
        check("L15b the record is bound to the code and inputs present now (no unaccepted drift)",
              not unaccepted, "; ".join(sr.format_drift(r) for r in unaccepted[:3]))
    else:
        check("L15b the inputs the corpus versions were built from carry no unaccepted drift "
              "(article files, manifest.csv)", not _inputs,
              "; ".join(sr.format_drift(r) for r in _inputs[:3]))
        # L15c reads the archived BYTES (Codex W1 round 2, #9): the tarball's code/build_sheets.py is
        # hashed here, not looked up in the archive record, and must be the frozen generator.
        import hashlib as _hl, tarfile as _tf
        _arch_p = Path(__file__).parent / "archive-manifest.json"
        _arch = json.loads(_arch_p.read_text(encoding="utf-8")) if _arch_p.exists() else {}
        _tar_p = Path(__file__).parents[2] / str((_arch.get("tarball") or {}).get("path", ""))
        _arch_code_bytes = None; _arch_registry = None
        if _tar_p.is_file():
            with _tf.open(_tar_p, "r:gz") as _tar:
                for _m in _tar.getmembers():
                    if _m.name.endswith("code/build_sheets.py"):
                        _arch_code_bytes = _tar.extractfile(_m).read()
                    elif _m.name.endswith("inputs/split-assignment.json"):
                        _arch_registry = json.loads(_tar.extractfile(_m).read().decode("utf-8"))
        _arch_code = _hl.sha256(_arch_code_bytes).hexdigest() if _arch_code_bytes else None
        check("L15c the frozen frame's generator is, byte for byte, the build_sheets.py the pre-freeze archive stores",
              bool(_gen) and _arch_code == _gen, f"frame {_gen[:16]} archived bytes {str(_arch_code)[:16]} tar={_tar_p.name}")
        # L15d derives the omitted acceptance records from the ARCHIVED pre-freeze registry: for every
        # corpus version, the build_sheets.py hash bound at registration versus the archived generator.
        def _bound_code(version: dict) -> str | None:
            # the generator hash a version was registered under: provenance.code_file_sha256
            return ((version.get("provenance") or {}).get("code_file_sha256") or {}).get("build_sheets.py")
        _derived = set()
        if _arch_registry and _arch_code:
            for _v in _arch_registry.get("corpus_versions", []):
                _rec = _bound_code(_v)
                if _rec and not _arch_code.startswith(_rec[:16]):
                    _derived.add((_v["version"], "code", "build_sheets.py", _rec[:16], _arch_code[:16]))
        OMITTED = {(v, "code", "build_sheets.py", "cc648f588ee9e0df", "0324e0fa7b5a6b36") for v in ("v1", "v2", "v3")}
        check("L15d the three buried acceptance records are exactly the drift the archived pre-freeze registry "
              "shows against the archived generator", _derived == OMITTED, f"derived {sorted(_derived)}")
        _now = {(r.get("version"), r.get("component"), r.get("key"), str(r.get("recorded"))[:16],
                 str(r.get("observed"))[:16]) for r in _code}
        _foreign = [c for c in _now if not (c[1] == "code" and c[2] == "build_sheets.py")]
        check("L15e unaccepted code records name no code file but the generator", not _foreign, str(_foreign[:3]))
        _buried = sorted(c[0] for c in _now & OMITTED); _later = sorted(c[0] for c in _now - OMITTED)
        print(f"        note: buried acceptance records present as such: {_buried or 'none'}; generator drift since "
              f"the freeze (recorded vs current code, not accepted because nothing can follow the terminal "
              f"event): {_later or 'none'}; frozen generator {_gen[:16]}")

    # Label files: row counts AND the hashes sessions.csv recorded at finalization.
    # A row count alone would accept an edited file, which is most of what could
    # go wrong to a committed label artifact.
    import hashlib
    ses = {r["labels_file"]: r for r in csv.DictReader(
        (Path(__file__).parent / "labels" / "sessions.csv").open(encoding="utf-8-sig"),
        delimiter=";")}
    # Every row in sessions.csv, not a hand-picked four. The earlier list omitted
    # A0_pass2 and A0b_pass2 while the check claimed to cover the record.
    check("L16  sessions.csv lists at least the six known finalized sessions",
          len(ses) >= 6, f"got {len(ses)}")
    for f, rec in sorted(ses.items()):
        path = Path(__file__).parent / "labels" / f
        check(f"L16a {f} exists", path.exists())
        if not path.exists():
            continue
        rowsL = list(csv.DictReader(path.open(encoding="utf-8-sig"), delimiter=";"))
        check(f"L16b {f} has the {rec['n_rows']} rows sessions.csv recorded",
              len(rowsL) == int(rec["n_rows"]), f"got {len(rowsL)}")
        got = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        check(f"L16c {f} matches the hash sessions.csv recorded",
              rec["labels_sha256"] == got, f"recorded {rec['labels_sha256']}, file {got}")

    # ALL_paragraphs.csv must agree with the registry, row for row
    ap = list(csv.DictReader((Path(__file__).parent / "sheets" / "ALL_paragraphs.csv")
                             .open(encoding="utf-8-sig"), delimiter=";"))
    # The full identity, not just the paragraph id: comparing ids and splits alone
    # would accept altered text, a changed digest, or a paragraph moved to another
    # article — the exact mutations the registry exists to detect.
    want = {(by_key[k]["article_id"], by_key[k]["paragraph_id"],
             by_key[k]["text_sha256"]): v for k, v in assigned.items()}
    got = {(r["article_id"], r["paragraph_id"], r["text_sha256"]): r["split"] for r in ap}
    check("L17 ALL_paragraphs.csv agrees with the registry on identity AND split",
          got == want,
          f"{len(set(got) ^ set(want))} identity differences, "
          f"{sum(1 for k in set(got) & set(want) if got[k] != want[k])} split differences")
    check("L17b ALL_paragraphs.csv has exactly one row per identity",
          len(ap) == len(got) == len(want), f"{len(ap)} rows, {len(got)} identities, "
          f"{len(want)} expected")
    check("L18 ALL_paragraphs.csv carries has_transcription_gap",
          "has_transcription_gap" in (ap[0].keys() if ap else ()))


# ─── P0, tested for real: run the builder against an injected corpus ─────────
def a2_pipeline() -> None:
    from collections import Counter  # noqa: F811

    def refuses(name: str, fn, needle: str) -> None:
        """expect_raises() is typed to the registry's own error; the draw has its own."""
        try:
            fn()
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            check(name, needle in str(exc), f"{type(exc).__name__}: {exc}")
        else:
            check(name, False, "did not raise")
    """Steps 1.5 and 1.6 of tools/docs/PIPELINE-COMPLETION-PLAN.md, exercised on a CLONE of the
    real registry with every output redirected to a temp dir. Nothing is written to
    the repository; the real freeze is the author's act. Assertions are on counts and
    on the handshake, never on which paragraphs were drawn."""
    import copy
    import tempfile
    import build_sheets as bs
    import draw_a2 as da

    print("\n── A2  frame manifest, freeze handshake, and the draw (fixture, dry run)")
    tmp = Path(tempfile.mkdtemp(prefix="a2_pipeline_"))
    rows, _ = bs.collect()
    real = sr.SplitRegistry.load()
    reg = clone(real)
    # The real reserve is frozen since 2026-09-06. The fixture replays the freeze, so it
    # starts from the state just before it: the terminal event is dropped from the clone
    # (a prefix of a valid chain is a valid chain). Nothing is written to the repository.
    if reg.a2_frozen() is not None:
        assert reg.assignment_log[-1].get("kind") == sr.EVENT_FREEZE, "freeze is not the head"
        reg.assignment_log = reg.assignment_log[:-1]
    routed = sr.route(reg, rows)
    assigned = routed["assigned"]

    # redirect every path the two modules write to
    bs.SPLIT_FILE = tmp / "split.json"
    bs.FRAME_MANIFEST = tmp / "A2_frame_manifest.json"
    bs.ARCHIVE_MANIFEST = tmp / "archive-manifest.json"
    bs.ARCHIVE_DIR = tmp / "archive"
    da.SAMPLE_MANIFEST = tmp / "A2_sample_manifest.json"
    reg.save(bs.SPLIT_FILE)                       # the archive tars the registry file

    # the freeze refuses without a verifiable archive (an empty record does not count)
    bs.ARCHIVE_MANIFEST.write_text("{}")
    refuses("A2-0 the freeze refuses an archive record that names no tarball",
            lambda: bs.freeze_a2(reg, rows, assigned), "does not stand")
    tar = bs.archive_corpus(reg, bs.read_manifest())          # real sources, temp tarball
    check("A2-0b --archive writes a tarball and a record whose corpus versions are the registry's",
          tar.exists() and not bs.verify_archive(reg), str(bs.verify_archive(reg)))
    rec_json = json.loads(bs.ARCHIVE_MANIFEST.read_text())
    if False:
        pass
    rec_tmp = json.loads(bs.ARCHIVE_MANIFEST.read_text()); rec_tmp["files_sha256"]["inputs/manifest.csv"] = "0" * 64
    bs.ARCHIVE_MANIFEST.write_text(json.dumps(rec_tmp))
    check("A2-0d an archive whose manifest.csv no longer matches the current one does not stand",
          any("manifest.csv" in p_ for p_ in bs.verify_archive(reg)))
    bs.ARCHIVE_MANIFEST.write_text(json.dumps(json.loads(bs.ARCHIVE_MANIFEST.read_text()) | {"files_sha256": rec_json["files_sha256"]})) if False else None
    tar = bs.archive_corpus(reg, bs.read_manifest())          # restore a standing archive
    check("A2-0c the archive record discloses the accepted drift and the unrecoverable v2 bytes",
          rec_json["accepted_binding_drift_records"] > 0 and "NOT" in rec_json["disclosure"]
          and any(k.startswith("code/") for k in rec_json["files_sha256"]))

    # A2-23: the freeze never buries unaccepted drift. On the clone, whatever drift the
    # real registry carries unaccepted must make the freeze refuse; after an explicit
    # acceptance it proceeds, and the acceptance precedes the terminal event on the log.
    _un = [r for r in bs.verify_input_bindings(reg, bs.read_manifest())
           if sr.drift_fingerprint(r) not in reg.accepted_drift()]
    if _un:
        refuses("A2-23 the freeze refuses while binding drift is unaccepted",
                lambda: bs.freeze_a2(reg, rows, assigned), "unaccepted binding drift")
        reg.append_drift_acceptance(_un, audit_ref="test-accept-before-freeze")
        reg.save(bs.SPLIT_FILE)
        tar = bs.archive_corpus(reg, bs.read_manifest())  # the archive follows the acceptance
    else:
        check("A2-23 the freeze refuses while binding drift is unaccepted (no drift to test on)", True)
    frame_doc = bs.freeze_a2(reg, rows, assigned)
    _kinds = [e.get("kind") for e in reg.assignment_log[-2:]]
    check("A2-23b no event follows the freeze, and the acceptance (if any) precedes it",
          _kinds[-1] == sr.EVENT_FREEZE and (not _un or _kinds[-2] == sr.EVENT_DRIFT), str(_kinds))
    check("A2-1 the frame excludes A011 and every non-reserve, gap-marked or non-candidate row",
          all(p["article_id"] != "A011" for p in frame_doc["paragraphs"])
          and frame_doc["n_eligible"] == sum(
              1 for r in rows if assigned.get((r["article_id"], r["paragraph_id"], r["digest_full"]))
              == sr.RESERVE and r["_candidate"] and not r["has_transcription_gap"]
              and r["article_id"] != "A011"),
          f"n_eligible={frame_doc['n_eligible']}")
    check("A2-2 the frame digest recomputes from its content",
          bs.canonical_digest(frame_doc) == frame_doc["digest"])
    head = reg.assignment_log[-1]
    check("A2-3 the registry head is the freeze, audit_ref = manifest digest, predecessor = pre_freeze_head",
          head.get("kind") == sr.EVENT_FREEZE and head.get("audit_ref") == frame_doc["digest"]
          and reg.assignment_log[-2].get("h") == frame_doc["pre_freeze_head"])
    check("A2-4 the saved clone reloads and verifies its chain",
          not sr.verify_chain(sr.SplitRegistry.load(bs.SPLIT_FILE).assignment_log))

    doc = da.draw(frame_doc, reg, dry_run=True)
    by_lang = doc["by_language"]
    check("A2-5 the draw yields 60 German and 60 English paragraphs",
          by_lang == {"de": 60, "en": 60}, str(by_lang))
    check("A2-6 no stratum exceeds its cap or its size",
          all(r["n_h"] <= min(doc["design"]["cap_used"][r["lang"]], r["N_h"])
              for r in doc["allocation"]))
    check("A2-7 the requested cap of 24 was feasible for both languages",
          doc["design"]["cap_used"] == {"de": 24, "en": 24}, str(doc["design"]["cap_used"]))
    check("A2-8 A011 is absent from the sample",
          all(q["article_id"] != "A011" for q in doc["paragraphs"]))
    rel = Counter(q["lang"] for q in doc["paragraphs"] if q["reliability_subsample"])
    check("A2-9 the reliability subsample is 20 + 20", rel == {"de": 20, "en": 20}, str(rel))
    check("A2-10 every sampled paragraph carries pi = n_h/N_h and weight = 1/pi",
          all(abs(q["pi"] - q["n_h"] / q["N_h"]) < 1e-12 and abs(q["weight"] * q["pi"] - 1) < 1e-9
              for q in doc["paragraphs"]))
    doc2 = da.draw(frame_doc, reg, dry_run=True)
    check("A2-11 the draw is reproducible: two runs give the same digest",
          doc2["digest"] == doc["digest"])
    check("A2-12 sitting order is a permutation 1..n", sorted(q["sitting_order"] for q in doc["paragraphs"])
          == list(range(1, doc["n"] + 1)))

    # the handshake refuses on every tampering the plan names
    bad = copy.deepcopy(frame_doc); bad["n_eligible"] += 1
    refuses("A2-13 an edited frame manifest is refused", lambda: da.draw(bad, reg, dry_run=True),
                  "does not match its digest")
    reg2 = clone(reg)
    reg2.assignment_log = sr.chain(reg2.assignment_log + [{"seq": reg2.assignment_log[-1]["seq"] + 1,
                                                          "kind": sr.EVENT_DRIFT, "fingerprints": [],
                                                          "records": [], "notes": [],
                                                          "audit_ref": "test"}])
    refuses("A2-14 any event after the freeze makes the frame stale and the draw refuses",
                  lambda: da.draw(frame_doc, reg2, dry_run=True), "not the freeze")
    bad2 = copy.deepcopy(frame_doc); bad2["pre_freeze_head"] = "0" * 16
    bad2["digest"] = bs.canonical_digest(bad2)
    reg3 = clone(reg)
    reg3.assignment_log[-1]["audit_ref"] = bad2["digest"]
    reg3.assignment_log = sr.chain(reg3.assignment_log)
    refuses("A2-15 a wrong predecessor hash is refused",
                  lambda: da.draw(bad2, reg3, dry_run=True), "predecessor")
    refuses("A2-16 freezing twice is refused",
                  lambda: reg.freeze_a2(frame_doc["eligible_checksum"], audit_ref="x"), "already frozen")
    # allocation arithmetic on a synthetic frame
    # the English frame as the round-1 review recounted it: capacity 24+24+24+22+11 = 105
    alloc = da.allocate({"nyt": 184, "economist": 108, "guardian": 68, "aljazeera": 22,
                         "wapo": 11}, 60, 24)
    check("A2-17 capped proportional allocation sums to the target and respects the cap",
          sum(alloc.values()) == 60 and max(alloc.values()) <= 24 and alloc["wapo"] <= 11
          and alloc["aljazeera"] <= 22, str(alloc))
    refuses("A2-18 an infeasible cap is refused by allocate()",
                  lambda: da.allocate({"a": 10, "b": 10}, 60, 24), "infeasible")
    check("A2-19 smallest_feasible_cap names the smallest feasible integer (for the refusal message)",
          da.smallest_feasible_cap({"a": 100, "b": 5}, 60, 24) == 55)
    thin = copy.deepcopy(frame_doc)
    thin["paragraphs"] = [p for p in thin["paragraphs"] if not (p["lang"] == "en" and p["outlet"] != "The New York Times")]
    thin["digest"] = bs.canonical_digest(thin)
    reg4 = clone(reg); reg4.assignment_log[-1]["audit_ref"] = thin["digest"]; reg4.assignment_log = sr.chain(reg4.assignment_log)
    refuses("A2-19b an infeasible registered cap is REFUSED, not raised silently",
            lambda: da.draw(thin, reg4, dry_run=True), "infeasible")
    amended = da.draw(thin, reg4, dry_run=True, cap=60, cap_amendment="test amendment")
    check("A2-19c an explicit amended cap is recorded in the sample manifest",
          amended["design"]["cap_used"]["en"] == 60 and amended["design"]["cap_amendment"] == "test amendment")
    tie = da.allocate({"x": 10, "y": 10, "z": 10}, 4, 24)
    check("A2-20 remainder ties break by N_h then by name, deterministically",
          tie == {"x": 2, "y": 1, "z": 1}, str(tie))
    bs.OUT = tmp / "sheets"
    tampered = dict(doc); tampered["n"] = 999
    refuses("A2-21 --from-manifest refuses a sample manifest whose content does not match its digest",
            lambda: bs.write_a2_sheets(tampered, rows, assigned, reg), "does not match its digest")
    bs.LABELS = tmp / "labels"; bs.LABELS.mkdir(exist_ok=True)
    bs.write_a2_sheets(doc, rows, assigned, reg)
    sheet = (bs.OUT / "A2_evaluation_sheet-1.csv")
    check("A2-22 --from-manifest writes the sheet-1 with 120 rows and the A2 session manifest (temp dir)",
          sheet.exists() and sum(1 for _ in sheet.open(encoding="utf-8-sig")) == 121
          and (bs.LABELS / "A2_manifest.json").exists())
    # ---- the route-B re-code sheet (recode_sheet.py) ------------------------------------- #
    import csv
    import recode_sheet as rs
    refuses("A2-24 the re-code sheet is refused before the first coding is finalised",
            lambda: rs.build_recode_sheet(doc, rows, reg, out_dir=bs.OUT, labels_dir=bs.LABELS), "not finalised")
    # a finalised first coding: both label files, recorded with their digests, every row dated
    import hashlib as _hl
    hdr1 = "paragraph_id;coded_at;tech_wind\n"; hdr2 = "row_id;paragraph_id;technology;coded_at\n"
    (bs.LABELS / "A2_pass1_v1.0.csv").write_text(hdr1 + "".join(f"{q['paragraph_id']};2026-09-16;0\n" for q in doc["paragraphs"]), encoding="utf-8")
    (bs.LABELS / "A2_pass2_v1.0.csv").write_text(hdr2 + "1;X;wind;20.09.26\n", encoding="utf-8")   # two-digit year, as finalize accepts
    def _rec(bad=False):
        d1 = _hl.sha256((bs.LABELS / "A2_pass1_v1.0.csv").read_bytes()).hexdigest()[:16]
        d2 = _hl.sha256((bs.LABELS / "A2_pass2_v1.0.csv").read_bytes()).hexdigest()[:16]
        (bs.LABELS / "sessions.csv").write_text("session;codebook_version;n_rows;annotators;coded_at;sittings;labels_file;labels_sha256;source_sheet;finalized_on\n"
                                              f"A2_pass1;1.0;120;NW;2026-09-16;1|2|3;A2_pass1_v1.0.csv;{'deadbeefdeadbeef' if bad else d1};s;2026-09-21\n"
                                              f"A2_pass2;1.0;200;NW;2026-09-20;4|5|6;A2_pass2_v1.0.csv;{d2};s;2026-09-21\n", encoding="utf-8")
    _rec(bad=True)
    refuses("A2-24b a first-coding file whose digest differs from its finalisation record is refused",
            lambda: rs.build_recode_sheet(doc, rows, reg, out_dir=bs.OUT, labels_dir=bs.LABELS), "differs from the digest")
    _rec()
    res = rs.build_recode_sheet(doc, rows, reg, out_dir=bs.OUT, labels_dir=bs.LABELS)
    check("A2-24c the latest first-coding date is the maximum over both passes as a date, ISO, legacy format included",
          res["last_first_coding_date"] == "2026-09-20")
    rsheet = bs.OUT / "A2R_reliability_sheet-1.csv"
    rrows = list(csv.DictReader(rsheet.open(encoding="utf-8-sig"), delimiter=";"))
    flagged = {q["paragraph_id"] for q in doc["paragraphs"] if q.get("reliability_subsample")}
    sitting = [q["paragraph_id"] for q in sorted((q for q in doc["paragraphs"] if q.get("reliability_subsample")), key=lambda q: q["sitting_order"])]
    check("A2-25 the re-code sheet holds exactly the flagged paragraphs, in a fresh order, with every label blank",
          res["rows"] == len(flagged) and {r["paragraph_id"] for r in rrows} == flagged
          and [r["paragraph_id"] for r in rrows] != sitting
          and all(r[c] in ("", "0") for r in rrows for c in bs.BINARY_COLUMNS) and all(not r["coded_at"] for r in rrows))
    rman = json.loads((bs.LABELS / "A2R_manifest.json").read_text())
    check("A2-26 the re-code session manifest names its origin, route, seed and washout, and the same text hashes as A2",
          rman["session"] == "A2R" and rman["recode"]["of_session"] == "A2" and rman["recode"]["seed_order"] == rs.SEED_RECODE
          and rman["recode"]["washout_days"] == 14
          and {q["paragraph_id"]: q["text_sha256"] for q in rman["paragraphs"]}
              == {q["paragraph_id"]: q["text_sha256"] for q in json.loads((bs.LABELS / "A2_manifest.json").read_text())["paragraphs"] if q["paragraph_id"] in flagged})
    refuses("A2-27 the re-code sheet is written once",
            lambda: rs.build_recode_sheet(doc, rows, reg, out_dir=bs.OUT, labels_dir=bs.LABELS), "generated once")
    (bs.OUT / "A2R_reliability_sheet-1.csv").unlink()
    (bs.OUT / "A2R_reliability_sheet-2.csv").write_text("coded pass 2 -- must survive", encoding="utf-8")
    refuses("A2-27b with sheet 1 gone but sheet 2 or the manifest present, generation is refused before anything is written",
            lambda: rs.build_recode_sheet(doc, rows, reg, out_dir=bs.OUT, labels_dir=bs.LABELS), "nothing is overwritten")
    check("A2-27c the existing coded sheet 2 was not touched",
          (bs.OUT / "A2R_reliability_sheet-2.csv").read_text(encoding="utf-8") == "coded pass 2 -- must survive")
    # ---- the second re-code wave (recode_sheet.py --wave 2, specification §4n) ------------------ #
    rest = [q for q in doc["paragraphs"] if not q.get("reliability_subsample")]
    rest_lang = {}
    for q in rest:
        rest_lang[q["lang"]] = rest_lang.get(q["lang"], 0) + 1
    d39 = tmp / "d39.csv"
    d39_ids = sorted(q["paragraph_id"] for q in rest)[:3] + [sorted(flagged)[0]]      # three exposed in the population, one outside
    d39.write_text("item;paragraph_id\n" + "".join(f"{i};{pid}\n" for i, pid in enumerate(d39_ids, 1)), encoding="utf-8")
    decl = {"paragraphs": len(rest), "by_language": rest_lang, "d39_exposed": 3}
    w2 = lambda **kw: rs.build_recode_sheet_wave2(doc, rows, reg, out_dir=bs.OUT, labels_dir=bs.LABELS, d39_sheet=d39,
                                                  declared=kw.get("declared", decl))
    refuses("A2-28 the second wave is refused before the route-B re-code is finalised", w2, "wave 1")
    (bs.LABELS / "A2R_pass1_v1.0.csv").write_text("paragraph_id;coded_at\nX;2026-10-13\n", encoding="utf-8")
    (bs.LABELS / "A2R_pass2_v1.0.csv").write_text("row_id;paragraph_id;technology;coded_at\n1;X;wind;2026-10-13\n", encoding="utf-8")
    with (bs.LABELS / "sessions.csv").open("a", encoding="utf-8") as fh:
        for f_ in ("A2R_pass1_v1.0.csv", "A2R_pass2_v1.0.csv"):
            fh.write(f"{f_.split('_v')[0]};1.0;1;NW;2026-10-13;1;{f_};{_hl.sha256((bs.LABELS / f_).read_bytes()).hexdigest()[:16]};s;2026-10-13\n")
    refuses("A2-28b a population that differs from the declared counts is refused",
            lambda: w2(declared={**decl, "paragraphs": decl["paragraphs"] + 1}), "not the declared")
    refuses("A2-28c a D39 overlap that differs from the declared count is refused",
            lambda: w2(declared={**decl, "d39_exposed": 4}), "not the declared")
    res2 = w2()
    s2 = list(csv.DictReader((bs.OUT / "A2R2_reliability_sheet-1.csv").open(encoding="utf-8-sig"), delimiter=";"))
    sit2 = [q["paragraph_id"] for q in sorted(rest, key=lambda q: q["sitting_order"])]
    check("A2-29 the second wave holds exactly the paragraphs outside the route-B subsample, in a fresh order, labels blank",
          res2["rows"] == len(rest) and {r["paragraph_id"] for r in s2} == {q["paragraph_id"] for q in rest}
          and [r["paragraph_id"] for r in s2] != sit2
          and all(r[c] in ("", "0") for r in s2 for c in bs.BINARY_COLUMNS) and all(not r["coded_at"] for r in s2))
    m2 = json.loads((bs.LABELS / "A2R2_manifest.json").read_text())
    check("A2-29b the second-wave manifest records wave, seed, origin and exactly the exposed population paragraphs",
          m2["session"] == "A2R2" and m2["recode"]["wave"] == 2 and m2["recode"]["seed_order"] == rs.SEED_RECODE2
          and m2["recode"]["of_session"] == "A2" and m2["recode"]["d39_exposed"] == d39_ids[:3])
    refuses("A2-29c the second-wave sheet is written once", w2, "generated once")
    import shutil; shutil.rmtree(tmp, ignore_errors=True)


def integration() -> None:
    """Run build_sheets.main() end to end on a synthetic corpus in a temp tree.

    The earlier P9.1 called `route()` and claimed to test P0. It did not: the sheet
    guard lives in `main()`, so nothing was exercised and no file was ever compared.
    This injects CORPUS, OUT and the registry path, writes sentinel sheets for the
    closed splits, runs the real builder, and compares bytes and mtimes.
    """
    import hashlib
    import importlib.util
    import os
    import time

    tmp = Path(tempfile.mkdtemp(prefix="p0-integration-"))
    corpus, out = tmp / "corpus", tmp / "sheets"
    (corpus / "articles").mkdir(parents=True)
    out.mkdir()

    def article(aid: str, outlet: str, lang: str, paras: list[str]) -> None:
        head = (f"<!--\nid: {aid}\noutlet: {outlet}\ncountry: DE\nlanguage: {lang}\n"
                f"publication_date: 2026-01-01\nheadline: H {aid}\n"
                f"text_status: full\ntechnologies: wind\nselected_for: test\n-->\n\n"
                f"# {aid}\n\n")
        (corpus / "articles" / f"{aid}.md").write_text(head + "\n\n".join(paras),
                                                       encoding="utf-8")

    def wind(i: int) -> str:
        return " ".join([f"x{i}y{j}" for j in range(20)] + ["wind"])

    article("A900", "taz", "de", [wind(1), wind(2)])
    article("A901", "taz", "de", [wind(3), wind(4)])
    article("A903", "taz", "de", [wind(5), wind(6)])
    (corpus / "manifest.csv").write_text(
        "id,batch,outlet,country,language,publication_date,headline,url,retrieved_at,"
        "technologies,selected_for,file\n"
        + "".join(f"{a},1,taz,DE,de,2026-01-01,H {a},u,2026-01-01,wind,test,"
                  f"articles/{a}.md\n" for a in ("A900", "A901", "A903")),
        encoding="utf-8")

    spec = importlib.util.spec_from_file_location("bs_i", str(Path(__file__).parent / "build_sheets.py"))
    bs = importlib.util.module_from_spec(spec)
    sys.argv = ["bs", "--report"]
    spec.loader.exec_module(bs)
    bs.CORPUS, bs.OUT, bs.SPLIT_FILE = corpus, out, tmp / "registry.json"
    sr.REGISTRY_FILE = tmp / "registry.json"

    rows, _ = bs.collect()
    for r in rows:
        r["digest_full"] = sr.digest(r["paragraph_text"])
    assign = {"A900": "A0", "A901": "A1", "A903": sr.RESERVE}
    members = [{"article_id": r["article_id"], "paragraph_id": r["paragraph_id"],
                "digest": r["digest_full"], "initial_split": assign[r["article_id"]]}
               for r in rows]
    data = {"schema_version": sr.SCHEMA_VERSION,
            # Real provenance and input bindings, so the build exercises the normal
            # path. A fixture with no bindings would be refused by the fail-closed
            # drift guard, and passing --accept-binding-drift to get around that
            # would test the override instead of the thing being tested.
            "corpus_versions": [{"version": "v1", "closed": True,
                                 "member_count": len(members),
                                 "checksum": sr.version_checksum(members),
                                 "provenance": bs._provenance(),
                                 "input_binding": bs._input_binding(bs.read_manifest()),
                                 "members": members}],
            "assignment_log": []}
    reg = sr.SplitRegistry(data)
    reg.append_assignments([((r["article_id"], r["paragraph_id"], r["digest_full"]),
                             assign[r["article_id"]]) for r in rows],
                           reason="integration fixture", audit_ref="test")
    reg.save(tmp / "registry.json")

    sentinels = {}
    for name in ("A0_calibration_sheet-1.csv", "A0_calibration_sheet-2.csv",
                 "A1_development_sheet-1.csv", "A1_development_sheet-2.csv"):
        q = out / name
        q.write_text("SENTINEL — must not be overwritten\n", encoding="utf-8")
        sentinels[name] = (hashlib.sha256(q.read_bytes()).hexdigest(), os.stat(q).st_mtime_ns)
    time.sleep(0.01)

    sys.argv = ["build_sheets"]
    bs.main()

    print("\n─── P0 integration (injected corpus) ───")
    for name, (h0, m0) in sentinels.items():
        pass  # compared below
    for name, (h0, m0) in sentinels.items():
        q = out / name
        h1, m1 = hashlib.sha256(q.read_bytes()).hexdigest(), os.stat(q).st_mtime_ns
        check(f"I1 {name} untouched by a real build (bytes + mtime)",
              h1 == h0 and m1 == m0,
              "bytes changed" if h1 != h0 else "mtime changed")
    ap = out / "ALL_paragraphs.csv"
    check("I2 the builder still wrote its derived reference sheet", ap.exists())
    check("I3 the injected registry was updated, not the live one",
          (tmp / "registry.json").exists()
          and sr.SplitRegistry.load(tmp / "registry.json").schema_version == 2)

    # A build with a NON-EMPTY quarantine. This is the case that would have caught
    # the partition arithmetic still summing `assigned + quarantined` after
    # quarantine moved inside `assigned`: every such build aborted before writing
    # anything, and the suite never noticed because the real quarantine is empty.
    recorded = next(r for r in rows if r["article_id"] == "A900")
    article("A904", "taz", "de",
            [recorded["paragraph_text"] + " ein zusaetzlicher satz wind", wind(9)])
    (corpus / "manifest.csv").write_text(
        (corpus / "manifest.csv").read_text(encoding="utf-8")
        + "A904,1,taz,DE,de,2026-01-01,H A904,u,2026-01-01,wind,test,articles/A904.md\n",
        encoding="utf-8")
    sys.argv = ["build_sheets"]
    bs.main()
    reg2 = sr.SplitRegistry.load(tmp / "registry.json")
    cur2 = reg2.current_splits()
    q = [k for k, v in cur2.items() if v == sr.QUARANTINE]
    check("I4 a build with a non-empty quarantine completes and PERSISTS it",
          len(q) == 1, f"{len(q)} quarantined identities recorded: {[k[1] for k in q]}")
    check("I5 the quarantined identity is a corpus member with its reason in the log",
          bool(q) and q[0] in reg2.members()
          and any(e.get("digest") == q[0][2] and "containment" in (e.get("reason") or "")
                  for e in reg2.assignment_log),
          "not a member, or no reason recorded")
    check("I6 the clean added paragraph in the same article still reached the reserve",
          sum(1 for k, v in cur2.items() if k[0] == "A904" and v == sr.RESERVE) == 1)
    for name, (h0, m0) in sentinels.items():
        qf = out / name
        check(f"I7 {name} still untouched after the second build",
              hashlib.sha256(qf.read_bytes()).hexdigest() == h0
              and os.stat(qf).st_mtime_ns == m0)


if __name__ == "__main__":
    if "--integration" in sys.argv:
        integration()
        print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
        sys.exit(1 if FAILED else 0)
    if "--live" in sys.argv:
        from collections import Counter
        live()
        print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
        sys.exit(1 if FAILED else 0)
    if "--a2" in sys.argv:
        # Reads the live corpus (gitignored text) and a CLONE of the live registry;
        # writes only into a temp dir. Run it like --live, on the system interpreter.
        from collections import Counter
        a2_pipeline()
        print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
        sys.exit(1 if FAILED else 0)
    main()
