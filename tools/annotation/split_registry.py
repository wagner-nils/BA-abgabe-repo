#!/usr/bin/env python3
"""The split registry: who is allowed into the evaluation reserve, and why.

This module owns every invariant that protects the gold standard. It exists as a
separate unit from `build_sheets.py` because the reasons it refuses things have to
be auditable on their own -- the thesis reports numbers that depend on them, and a
reader who wants to check that already-seen text cannot reach the evaluation set
should be able to read one file rather than a 500-line sheet builder.

Written after six rounds of adversarial review; the argument is in
`tools/CORPUS-INTEGRATION-REVIEW-LOG.md` and the settled design in
`tools/CORPUS-INTEGRATION-PLAN.md`. Section markers below cite the plan step.

THE MODEL, in one paragraph. Corpus **membership** and split **assignment** are
separate records. Membership is a list of immutable versions; each version records
only the identities it added, and an identity is `(article_id, paragraph_id,
sha256)` -- all three, because a hash alone would let an unchanged paragraph moved
into another article inherit its old split while its provenance changed. Assignment
is an append-only, checksum-chained log of `(identity, split, reason, audit_ref)`
events; the current split of an identity is the last event naming it. A0, A0b, A1
and A1b are terminal: nothing transitions out of them, ever. The single permitted
transition is an audited `RESERVED_A2 -> A1b`, and only before the terminal
`a2_frozen` event is appended.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGISTRY_FILE = HERE / "split-assignment.json"

SCHEMA_VERSION = 2

# ─── the split vocabulary ────────────────────────────────────────────────────
# Terminal means: an identity that has ever been assigned here never moves. A0,
# A0b and A1 have been coded; A1b will be used to tune the prompt. Letting any of
# them transition back into the reserve would put prompt-seen text into the
# evaluation set, which is the one failure this module exists to prevent.
TERMINAL_SPLITS = frozenset({"A0", "A0b", "A1", "A1b"})
RESERVE = "RESERVED_A2"
QUARANTINE = "QUARANTINE_ADJUDICATE"
ALL_SPLITS = frozenset(TERMINAL_SPLITS | {RESERVE, QUARANTINE})

# Quarantine is a real split with a real membership record, not a side list. An
# earlier version kept it as a mutable top-level array rewritten on every build,
# which meant a quarantined paragraph that later disappeared vanished from the
# audit trail without the disappearance check noticing. Now it is a member like
# any other and adjudication is an append-only transition.
PERMITTED_TRANSITIONS = frozenset({
    (RESERVE, "A1b"),          # the A1b withdrawal, audited, before the freeze
    (QUARANTINE, RESERVE),     # adjudicated: judged not a re-insertion
    (QUARANTINE, "A1b"),       # adjudicated into development instead
})

EVENT_ASSIGN = "assign"
EVENT_FREEZE = "a2_frozen"
# Accepting input drift is an EVENT, not a field on the next corpus version. It
# used to be recorded on the new version block -- which is only written when there
# are fresh identities, so accepting metadata-only drift recorded nothing at all
# and every later run saw the same unacknowledged drift.
EVENT_DRIFT = "binding_drift_accepted"
EVENT_KINDS = frozenset({EVENT_ASSIGN, EVENT_FREEZE, EVENT_DRIFT})

# ─── P3a: the containment check, fully specified ─────────────────────────────
# Codex round 5 was right that an unspecified check makes the reserve counts
# unstable: two implementations would quarantine different subsets, and the
# reported denominator would depend on which one ran. Every knob is fixed here.
#
# Direction matters. The question is "how much of a RECORDED paragraph reappears
# in this new one", not the reverse -- a long new paragraph that swallows a short
# recorded one is exactly the bypass this catches, and normalising by the new
# paragraph's length would hide it.
SHINGLE_WORDS = 8
CONTAINMENT_THRESHOLD = 0.15          # inclusive: >= quarantines
_NON_WORD = re.compile(r"[^\w\s]+", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """NFKC, casefold, non-word to space, collapse whitespace.

    `\\w` semantics depend on the Unicode database the interpreter ships, so the
    Python minor version is recorded in every corpus version alongside the
    algorithm version. Changing either invalidates comparability, deliberately.
    """
    t = unicodedata.normalize("NFKC", text).casefold()
    t = _NON_WORD.sub(" ", t)
    return _WHITESPACE.sub(" ", t).strip()


def shingles(text: str, k: int = SHINGLE_WORDS) -> set[str]:
    """k-word shingles. A paragraph shorter than k yields none, on purpose."""
    words = normalize(text).split()
    if len(words) < k:
        return set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def digest(text: str) -> str:
    """Full SHA-256. The legacy record truncated to 16 hex; that is a collision
    surface with no upside, and the truncation is what let a dict silently
    collapse two paragraphs into one entry."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def identity(article_id: str, paragraph_id: str, text: str) -> tuple[str, str, str]:
    return (article_id, paragraph_id, digest(text))


# ─── the chained log ─────────────────────────────────────────────────────────
def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _link(prev_hash: str, entry: dict) -> str:
    """Hash of (previous link, this entry without its own link)."""
    body = {k: v for k, v in entry.items() if k != "h"}
    return hashlib.sha256(prev_hash.encode() + _canonical(body)).hexdigest()[:16]


def chain(entries: list[dict]) -> list[dict]:
    """Recompute the chain over entries, returning them with `h` set."""
    out, prev = [], ""
    for e in entries:
        e = dict(e)
        e["h"] = _link(prev, e)
        prev = e["h"]
        out.append(e)
    return out


def verify_chain(entries: list[dict]) -> list[str]:
    """Return a list of problems; empty means the chain is intact."""
    problems, prev = [], ""
    for i, e in enumerate(entries):
        want = _link(prev, e)
        got = e.get("h", "")
        if got != want:
            problems.append(
                f"assignment_log entry {i} (seq {e.get('seq', '?')}) has link {got or '<missing>'}, "
                f"expected {want}. An entry was edited, reordered or inserted.")
            return problems          # everything after this is unverifiable
        prev = got
    return problems


def validate_log(entries: list[dict], members: set[tuple[str, str, str]] | None = None,
                 initial: dict[tuple[str, str, str], str] | None = None) -> list[str]:
    """Replay the whole log against the state machine. This is the real guard.

    The link chain is NOT a security boundary and must not be mistaken for one:
    there is no secret, so anyone holding this code can re-chain a tampered log.
    What the chain buys is detection of *accidental* edits and, together with the
    stored-prefix rule in `save()`, enforcement of append-only. What actually
    makes a tampered log unusable is replaying it: sequence numbers must be dense
    and ascending, every event kind known, every assignment must name a recorded
    member, each member must have exactly one initial assignment, every
    subsequent assignment must be a permitted transition, and nothing may follow
    the freeze. A log that merely re-hashes cleanly still fails all of that.
    """
    problems: list[str] = []
    seen_seq: set[int] = set()
    state: dict[tuple[str, str, str], str] = {}
    frozen_at: int | None = None
    last_seq = 0

    for i, e in enumerate(entries):
        kind = e.get("kind")
        seq = e.get("seq")
        if not isinstance(seq, int):
            problems.append(f"entry {i} has non-integer seq {seq!r}")
            continue
        if seq in seen_seq:
            problems.append(f"entry {i} repeats seq {seq}")
        if seq != last_seq + 1:
            problems.append(f"entry {i} has seq {seq}, expected {last_seq + 1} "
                            f"(sequence numbers must be dense and ascending)")
        seen_seq.add(seq)
        last_seq = seq

        if kind == EVENT_FREEZE:
            if frozen_at is not None:
                problems.append(f"seq {seq} freezes an already-frozen evaluation set "
                                f"(first freeze at seq {frozen_at})")
            got_ck = e.get("eligible_checksum")
            if not got_ck:
                problems.append(f"seq {seq} is a freeze without an eligible_checksum")
            else:
                # Recomputed from the replayed state, not merely checked non-empty.
                # A freeze event carrying a made-up checksum used to load and save
                # cleanly, which made the one event that finalises evaluation
                # membership unverifiable after the fact.
                want_ck = eligible_checksum_of(dict(state))
                if got_ck != want_ck:
                    problems.append(
                        f"seq {seq} freezes with eligible_checksum {got_ck!r}, but the reserve "
                        f"as replayed to that point hashes to {want_ck!r}")
            frozen_at = seq
            continue
        if kind == EVENT_DRIFT:
            if not e.get("fingerprints") or not e.get("audit_ref"):
                problems.append(f"seq {seq} accepts binding drift without state fingerprints "
                                f"or an audit_ref")
            recs = e.get("records") or []
            # Exact MULTISET equality, in both directions. Checking only that every
            # record finds its fingerprint, plus equal lengths, let records [A, A]
            # sit beside fingerprints [fp(A), fp(B)]: both records matched fp(A),
            # the counts agreed, and fp(B) was authorized with no record behind it
            # -- so accepted_drift() would wave through a state nobody accepted.
            stored_fp = sorted(e.get("fingerprints") or [])
            derived_fp = sorted(drift_fingerprint(r) for r in recs)
            if stored_fp != derived_fp:
                orphans = sorted(set(stored_fp) - set(derived_fp))
                problems.append(
                    f"seq {seq}: the stored fingerprints do not correspond exactly to its "
                    f"{len(recs)} drift record(s)"
                    + (f"; {len(orphans)} fingerprint(s) have no record: {orphans[:4]}"
                       if orphans else "")
                    + ". A fingerprint with no record authorizes an unrecorded state.")
            stored_notes = sorted(e.get("notes") or [])
            derived_notes = sorted(format_drift(r) for r in recs)
            if stored_notes != derived_notes:
                problems.append(f"seq {seq}: the stored notes do not correspond to its drift "
                                f"records, so the human-readable trail and the machine-checked "
                                f"one disagree")
            continue
        if kind != EVENT_ASSIGN:
            problems.append(f"seq {seq} has unknown kind {kind!r} (known: "
                            f"{sorted(EVENT_KINDS)})")
            continue

        for field in ("article_id", "paragraph_id", "digest", "split", "reason", "audit_ref"):
            if not e.get(field):
                problems.append(f"seq {seq} is missing {field}")
        key = (e.get("article_id"), e.get("paragraph_id"), e.get("digest"))
        split = e.get("split")

        if frozen_at is not None:
            problems.append(f"seq {seq} assigns {key[1]} -> {split} AFTER the freeze at "
                            f"seq {frozen_at}. Nothing may follow it.")
        if split not in ALL_SPLITS:
            problems.append(f"seq {seq} assigns {key[1]} to unknown split {split!r}")
            continue
        if members is not None and key not in members:
            problems.append(f"seq {seq} assigns {key[1]}, which is not a recorded corpus "
                            f"member. Every assignment must name an identity that exists.")
        old = state.get(key)
        if old is None:
            if initial is not None and key in initial and initial[key] != split:
                problems.append(
                    f"seq {seq} records {key[1]}'s FIRST assignment as {split}, but the "
                    f"membership delta anchors it to {initial[key]}. The log was rewritten.")
            state[key] = split
            continue
        if old == split:
            problems.append(f"seq {seq} restates {key[1]} -> {split} with no change; "
                            f"the log must not carry no-op events")
            continue
        if old in TERMINAL_SPLITS:
            problems.append(f"seq {seq} moves {key[1]} out of terminal split {old} to {split}")
            continue
        if (old, split) not in PERMITTED_TRANSITIONS:
            problems.append(f"seq {seq} performs {old} -> {split} for {key[1]}, which is not "
                            f"in the allowlist {sorted(PERMITTED_TRANSITIONS)}")
            continue
        state[key] = split

    if members is not None:
        unassigned = sorted(k[1] for k in members if k not in state)
        if unassigned:
            problems.append(f"{len(unassigned)} recorded member(s) have no assignment event: "
                            f"{unassigned[:6]}{' ...' if len(unassigned) > 6 else ''}")
    return problems


def version_checksum(members: list[dict]) -> str:
    """Checksum over a membership delta, INCLUDING each member's initial split.

    Covering the initial split is what makes it an anchor: an edit that rewrites
    where a paragraph started changes this checksum and `check_versions()` refuses
    the registry.
    """
    return hashlib.sha256(_canonical(sorted(
        (m["article_id"], m["paragraph_id"], m["digest"], m.get("initial_split", ""))
        for m in members))).hexdigest()[:16]


# ─── the registry ────────────────────────────────────────────────────────────
class RegistryError(RuntimeError):
    """Raised where continuing would put already-seen text into the reserve, or
    would absorb a mutation silently. Never caught inside this module."""


class SplitRegistry:
    """`data` is the single backing store. `corpus_versions` and `assignment_log`
    are views onto it, not copies.

    They used to be plain attributes read out of `data` at construction. Because
    `chain()` returns a NEW list, `append_assignments` rebound the attribute while
    `data` kept the old un-chained list -- so anything reading `data` (a clone, a
    serialization that skipped save()) saw entries with no link at all. A contract
    test then appeared to detect a tampered entry when what it had actually caught
    was the missing link. Two names for one thing is how a record and its audit
    trail drift apart, so there is now only one.
    """

    def __init__(self, data: dict):
        self.data = data
        self.schema_version = data.get("schema_version", 1)
        data.setdefault("corpus_versions", [])
        data.setdefault("assignment_log", [])

    @property
    def corpus_versions(self) -> list[dict]:
        return self.data["corpus_versions"]

    @corpus_versions.setter
    def corpus_versions(self, v: list[dict]) -> None:
        self.data["corpus_versions"] = v

    @property
    def assignment_log(self) -> list[dict]:
        return self.data["assignment_log"]

    @assignment_log.setter
    def assignment_log(self, v: list[dict]) -> None:
        self.data["assignment_log"] = v

    # ---- loading ----------------------------------------------------------- #
    @classmethod
    def load(cls, path: Path | None = None) -> "SplitRegistry":
        # Resolved at CALL time, not captured as a default. A default argument
        # binds the module constant when the function is DEFINED, so reassigning
        # REGISTRY_FILE -- the only way a test can point this at a temporary tree
        # -- had no effect and the live record was read instead. That is how the
        # first P0 integration test came to validate the real registry against a
        # six-paragraph synthetic corpus.
        path = path or REGISTRY_FILE
        if not path.exists():
            raise RegistryError(f"no registry at {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        reg = cls(data)
        if reg.schema_version != SCHEMA_VERSION:
            raise RegistryError(
                f"{path.name} is schema_version {reg.schema_version}, this code speaks "
                f"{SCHEMA_VERSION}. Run migrate_split_registry.py -- and note that the "
                f"legacy by_article / by_text_sha256 fields are NOT read by assignment "
                f"code once migrated; they are kept only inside legacy_snapshot.")
        problems = verify_chain(reg.assignment_log)
        snap = data.get("legacy_snapshot") or {}
        raw = snap.get("raw_json")
        if raw is not None:
            got = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            if got != snap.get("checksum_sha256"):
                problems.append(
                    f"legacy_snapshot.raw_json hashes to {got[:16]}… but the record claims "
                    f"{str(snap.get('checksum_sha256'))[:16]}…. The preserved pre-migration "
                    f"state has been altered, so the migration is no longer reversible from "
                    f"this file.")
        problems += reg.check_versions()
        problems += reg.check_anchor_coverage()
        problems += validate_log(reg.assignment_log, set(reg.members()), reg.initial_splits())
        if problems:
            raise RegistryError(
                f"the registry is not valid ({len(problems)} problem(s)):\n  "
                + "\n  ".join(problems[:12])
                + (f"\n  … {len(problems) - 12} more" if len(problems) > 12 else ""))
        return reg

    # ---- membership integrity ---------------------------------------------- #
    def check_versions(self) -> list[str]:
        """Every version's recorded checksum and count must match its members."""
        problems: list[str] = []
        names = [v.get("version") for v in self.corpus_versions]
        if len(set(names)) != len(names):
            problems.append(f"duplicate corpus version name(s) in {names}")
        for v in self.corpus_versions:
            ms = v.get("members", [])
            if v.get("member_count") != len(ms):
                problems.append(f"version {v.get('version')} says member_count "
                                f"{v.get('member_count')} but carries {len(ms)} members")
            # The anchor is REQUIRED, not optional. version_checksum() substituted
            # "" for a missing initial_split, so an anchor could be deleted and the
            # checksum recomputed and everything still passed -- which defeats the
            # whole point of anchoring the initial mapping outside the log.
            bad_anchor = [m.get("paragraph_id", "?") for m in ms
                          if m.get("initial_split") not in ALL_SPLITS]
            if bad_anchor:
                problems.append(
                    f"version {v.get('version')}: {len(bad_anchor)} member(s) have no valid "
                    f"initial_split anchor: {bad_anchor[:6]}. Every member must record the "
                    f"split it was first assigned to.")
            b = v.get("input_binding") or {}
            fh = b.get("file_sha256")
            if not fh:
                problems.append(
                    f"version {v.get('version')} has no file_sha256 binding, so the article "
                    f"content it was computed from cannot be checked at all. Removing this "
                    f"binding used to pass every guard while destroying the evidence.")
            elif b.get("referenced_files") != len(fh):
                problems.append(f"version {v.get('version')} says referenced_files "
                                f"{b.get('referenced_files')} but binds {len(fh)} file(s)")
            want = version_checksum(ms)
            if v.get("checksum") != want:
                problems.append(f"version {v.get('version')} checksum {v.get('checksum')} "
                                f"does not match its members (computed {want}). The membership "
                                f"delta was edited.")
        return problems

    # ---- membership -------------------------------------------------------- #
    def members(self) -> dict[tuple[str, str, str], str]:
        """Identity triple -> the corpus version that introduced it."""
        out: dict[tuple[str, str, str], str] = {}
        for v in self.corpus_versions:
            for m in v["members"]:
                key = (m["article_id"], m["paragraph_id"], m["digest"])
                if key in out:
                    raise RegistryError(
                        f"identity {key[1]} appears in corpus versions {out[key]} and "
                        f"{v['version']}. Membership deltas must not overlap.")
                out[key] = v["version"]
        return out

    def check_anchor_coverage(self) -> list[str]:
        """Every member must be anchored, and nothing else may be."""
        m, i = set(self.members()), set(self.initial_splits())
        problems = []
        if m - i:
            problems.append(f"{len(m - i)} member(s) have no initial_split anchor: "
                            f"{sorted(k[1] for k in m - i)[:6]}")
        if i - m:
            problems.append(f"{len(i - m)} anchor(s) name a non-member: "
                            f"{sorted(k[1] for k in i - m)[:6]}")
        return problems

    def initial_splits(self) -> dict[tuple[str, str, str], str]:
        """Identity -> the split it was FIRST assigned to, from the membership delta.

        This is the anchor the log is checked against. Replaying transitions alone
        could not catch a rewritten *initial* assignment: editing the first A0
        event to say RESERVED_A2 and re-chaining produced a log that both the chain
        check and the transition replay accepted, and the paragraph moved from a
        coded set into the evaluation reserve. The initial mapping therefore lives
        in the membership delta, whose checksum covers it, so changing it breaks
        `check_versions()`.
        """
        out: dict[tuple[str, str, str], str] = {}
        for v in self.corpus_versions:
            for m in v["members"]:
                if "initial_split" in m:
                    out[(m["article_id"], m["paragraph_id"], m["digest"])] = m["initial_split"]
        return out

    def digests(self) -> dict[str, tuple[str, str, str]]:
        return {k[2]: k for k in self.members()}

    def closed_article_ids(self) -> set[str]:
        """Articles whose ENTIRE membership belongs to a terminal split.

        Deliberately five, not fourteen. The nine articles that merely CONTAIN an
        A0b paragraph also hold between 2 and 40 reserve paragraphs each (A010 has
        40); closing them would abort the build on any future paragraph appearing
        in exactly the articles that constitute the reserve. Those nine are
        handled by unconditional quarantine of unrecognised identities instead --
        see `mixed_article_ids`.
        """
        current = self.current_splits()
        by_article: dict[str, set[str]] = defaultdict(set)
        for (aid, _pid, dig), split in current.items():
            by_article[aid].add(split)
        return {a for a, splits in by_article.items() if splits <= TERMINAL_SPLITS}

    def closed_split_names(self) -> set[str]:
        """Splits that must not receive new members, and whose sheets must not be
        rewritten. A split is closed once any identity has been assigned to it and
        it is terminal -- A0, A0b, A1 have been coded, A1b will have been."""
        present = set(self.current_splits().values())
        return {s for s in present if s in TERMINAL_SPLITS}

    def mixed_article_ids(self) -> set[str]:
        """Articles holding both a terminal-split paragraph and a reserve one."""
        current = self.current_splits()
        by_article: dict[str, set[str]] = defaultdict(set)
        for (aid, _pid, dig), split in current.items():
            by_article[aid].add(split)
        return {a for a, s in by_article.items() if (s & TERMINAL_SPLITS) and (s - TERMINAL_SPLITS)}

    # ---- assignment -------------------------------------------------------- #
    def current_splits(self) -> dict[tuple[str, str, str], str]:
        """Identity -> its split, defined as the last assign event naming it."""
        out: dict[tuple[str, str, str], str] = {}
        for e in self.assignment_log:
            if e.get("kind") != EVENT_ASSIGN:
                continue
            out[(e["article_id"], e["paragraph_id"], e["digest"])] = e["split"]
        return out

    def a2_frozen(self) -> dict | None:
        for e in self.assignment_log:
            if e.get("kind") == EVENT_FREEZE:
                return e
        return None

    def split_of_digest(self, dig: str) -> str | None:
        for key, split in self.current_splits().items():
            if key[2] == dig:
                return split
        return None

    # ---- the transition state machine -------------------------------------- #
    def check_transition(self, key: tuple[str, str, str], new_split: str) -> None:
        """Refuse anything the allowlist does not name. Raises RegistryError."""
        if new_split not in ALL_SPLITS:
            raise RegistryError(f"{new_split!r} is not a split this registry knows: "
                                f"{sorted(ALL_SPLITS)}")
        frozen = self.a2_frozen()
        old = self.current_splits().get(key)
        if frozen is not None:
            raise RegistryError(
                f"the evaluation set was frozen by event seq {frozen['seq']} "
                f"({frozen.get('audit_ref', '?')}); no assignment event is permitted after it. "
                f"Refused: {key[1]} -> {new_split}.")
        if old is None:
            return                                  # initial assignment, always allowed
        if old == new_split:
            return                                  # idempotent re-statement
        if old in TERMINAL_SPLITS:
            raise RegistryError(
                f"{key[1]} is in {old}, which is terminal: it has been coded or used to tune "
                f"the prompt. Moving it to {new_split} would put seen text into a set it must "
                f"not reach. Refused.")
        if (old, new_split) not in PERMITTED_TRANSITIONS:
            raise RegistryError(
                f"transition {old} -> {new_split} for {key[1]} is not in the allowlist "
                f"{sorted(PERMITTED_TRANSITIONS)}. Refused.")

    # ---- writing ----------------------------------------------------------- #
    def append_assignments(self, assignments: list[tuple[tuple[str, str, str], str]],
                           reason: str, audit_ref: str,
                           reasons: dict[tuple[str, str, str], str] | None = None) -> int:
        """Validate every transition, then append. All-or-nothing.

        `reasons` supplies a per-identity reason where one exists -- a quarantine
        needs its own, because "why is this paragraph held back" is the whole
        content of the record a human later adjudicates from. Storing them in the
        log entry rather than in a side list is what makes them append-only: an
        earlier version kept a mutable top-level array that every build rewrote,
        so a quarantined paragraph could disappear from the audit trail entirely.
        """
        for key, split in assignments:
            self.check_transition(key, split)
        seq = max((e["seq"] for e in self.assignment_log), default=0)
        current = self.current_splits()
        added = 0
        for key, split in assignments:
            if current.get(key) == split:
                continue                            # already recorded, do not duplicate
            aid, pid, dig = key
            seq += 1
            self.assignment_log.append({
                "seq": seq, "kind": EVENT_ASSIGN, "article_id": aid, "paragraph_id": pid,
                "digest": dig, "split": split,
                "reason": (reasons or {}).get(key) or reason, "audit_ref": audit_ref,
            })
            current[key] = split
            added += 1
        self.assignment_log = chain(self.assignment_log)
        return added

    def accepted_drift(self) -> set[str]:
        """Fingerprints of every drift record explicitly accepted, from the log."""
        out: set[str] = set()
        for e in self.assignment_log:
            if e.get("kind") == EVENT_DRIFT:
                out.update(e.get("fingerprints") or [])
        return out

    def append_drift_acceptance(self, records: list[dict], audit_ref: str) -> None:
        """Record an acceptance keyed to the OBSERVED STATE, not to its wording.

        Keying on the human-readable note was wrong and quietly so: changing an
        article's language to `en` and later to `fr` produces the same sentence, so
        accepting the first change accepted every later one. A fingerprint over
        (version, component, key, recorded, observed) means each distinct drift
        needs its own acceptance, and a second change re-blocks.
        """
        seq = max((e["seq"] for e in self.assignment_log), default=0) + 1
        self.assignment_log.append({
            "seq": seq, "kind": EVENT_DRIFT,
            "fingerprints": sorted(drift_fingerprint(r) for r in records),
            "records": sorted(records, key=lambda r: (r["version"], r["component"], r["key"])),
            "notes": sorted(format_drift(r) for r in records),
            "audit_ref": audit_ref,
        })
        self.assignment_log = chain(self.assignment_log)

    def freeze_a2(self, eligible_checksum: str, audit_ref: str) -> None:
        """Freeze the evaluation set. The checksum is RECOMPUTED, not trusted.

        An earlier version accepted whatever the caller passed -- the test suite
        froze with the literal string "deadbeef" and it was recorded. A checksum
        nobody verifies is decoration on the one event that is supposed to make
        evaluation membership final.
        """
        if self.a2_frozen() is not None:
            raise RegistryError("the evaluation set is already frozen.")
        want = eligible_checksum_of(self.current_splits())
        if eligible_checksum != want:
            raise RegistryError(
                f"refusing to freeze: the supplied eligible_checksum {eligible_checksum!r} does "
                f"not match the reserve as recorded ({want!r}). Recompute it from the registry "
                f"with eligible_checksum_of(current_splits()) rather than passing a constant.")
        seq = max((e["seq"] for e in self.assignment_log), default=0) + 1
        self.assignment_log.append({
            "seq": seq, "kind": EVENT_FREEZE, "eligible_checksum": eligible_checksum,
            "audit_ref": audit_ref,
        })
        self.assignment_log = chain(self.assignment_log)

    def save(self, path: Path | None = None) -> None:
        """Atomic replace, and refuse to shorten or rewrite the stored log.

        `write_text` is not atomic and a torn write would leave the disjointness
        record unreadable. More importantly, the stored log must remain an exact
        PREFIX of what is written: that, plus the link chain, is what makes
        append-only an enforced property rather than a promise.
        """
        path = path or REGISTRY_FILE
        problems = (self.check_versions() + self.check_anchor_coverage()
                    + validate_log(self.assignment_log, set(self.members()),
                                   self.initial_splits()))
        if problems:
            raise RegistryError(
                f"refusing to write an invalid registry ({len(problems)} problem(s)):\n  "
                + "\n  ".join(problems[:10]))
        if path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            old_versions = old.get("corpus_versions", [])
            if len(self.corpus_versions) < len(old_versions):
                raise RegistryError(
                    f"refusing to write: stored corpus_versions has {len(old_versions)} entries "
                    f"and the new one {len(self.corpus_versions)}. Membership is append-only.")
            for i, (a, b) in enumerate(zip(old_versions, self.corpus_versions)):
                if a != b:
                    raise RegistryError(
                        f"refusing to write: stored corpus version {i} "
                        f"({a.get('version')}) differs from the new one. Versions are immutable.")
            old_log = old.get("assignment_log", [])
            new_log = self.assignment_log
            if len(new_log) < len(old_log):
                raise RegistryError(
                    f"refusing to write: the stored log has {len(old_log)} entries and the "
                    f"new one {len(new_log)}. The log is append-only.")
            for i, (a, b) in enumerate(zip(old_log, new_log)):
                if a != b:
                    raise RegistryError(
                        f"refusing to write: stored log entry {i} (seq {a.get('seq')}) differs "
                        f"from the new one. The stored log must stay an exact prefix.")
        self.data["schema_version"] = SCHEMA_VERSION
        self.data["corpus_versions"] = self.corpus_versions
        self.data["assignment_log"] = self.assignment_log
        payload = json.dumps(self.data, indent=1, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".registry-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


# ─── P4: verify every closed version against the collected corpus ────────────
def verify_closed_versions(reg: SplitRegistry, rows: list[dict]) -> None:
    """Every recorded identity must still resolve. Raises on the first failure.

    A disappeared identity means a paragraph's text changed or it was renumbered.
    Absorbing that silently is how previously-seen content re-enters under a new
    identity, so it aborts rather than warns -- and it is what protects A0b's
    twelve paragraphs inside reserve articles without closing those articles.
    """
    present = {(r["article_id"], r["paragraph_id"], r["digest_full"]) for r in rows}
    missing: list[tuple[str, str]] = []
    for key, version in reg.members().items():
        if key not in present:
            missing.append((version, key[1]))
    if missing:
        by_v = Counter(v for v, _ in missing)
        detail = ", ".join(pid for _, pid in missing[:8])
        raise RegistryError(
            f"{len(missing)} recorded identit{'y' if len(missing) == 1 else 'ies'} no longer "
            f"resolve in the corpus ({dict(by_v)}): {detail}"
            f"{' ...' if len(missing) > 8 else ''}.\n"
            f"A recorded paragraph's text changed, or it was renumbered. This is not absorbed: "
            f"a changed identity is how already-coded content re-enters the reserve under a new "
            f"hash. Restore the text, or adjudicate explicitly.")


# ─── P3 / P3a / P5: routing ──────────────────────────────────────────────────
def route(reg: SplitRegistry, rows: list[dict]) -> dict:
    """Decide a split for every collected paragraph. Pure: writes nothing.

    Returns a dict with `assigned` (identity -> split), `quarantined`
    (identity -> reason) and `stats`. Raises RegistryError on the abort cases.
    """
    # Called here, not left to the caller. If a recorded paragraph is absent from
    # `rows`, its text never enters the containment index below -- so omitting a
    # recorded paragraph and re-inserting its text elsewhere would slip past the
    # near-duplicate guard entirely. The check that makes that impossible has to
    # be part of routing, not a convention the caller is trusted to follow.
    verify_closed_versions(reg, rows)
    members = reg.members()
    current = reg.current_splits()
    closed_arts = reg.closed_article_ids()
    mixed_arts = reg.mixed_article_ids()

    # P5: an identity triple occurring twice is a collection bug, not data.
    seen: dict[tuple[str, str, str], int] = Counter(
        (r["article_id"], r["paragraph_id"], r["digest_full"]) for r in rows)
    dupe_ids = [k for k, n in seen.items() if n > 1]
    if dupe_ids:
        raise RegistryError(
            f"{len(dupe_ids)} identity triple(s) occur more than once in one collection: "
            f"{[k[1] for k in dupe_ids[:6]]}. That is a segmentation or manifest bug, not a "
            f"corpus property -- two rows cannot be the same paragraph.")

    # P5: two DIFFERENT triples sharing a digest is legitimate (agency copy, a
    # quote printed twice) and is quarantined rather than fatal.
    by_digest: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_digest[r["digest_full"]].append(r)

    # P3a: index recorded shingles once, then test each unrecognised paragraph.
    recorded_text = {}
    for r in rows:
        key = (r["article_id"], r["paragraph_id"], r["digest_full"])
        if key in members:
            recorded_text[key] = r["paragraph_text"]
    index: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    recorded_size: dict[tuple[str, str, str], int] = {}
    for key, text in recorded_text.items():
        sh = shingles(text)
        recorded_size[key] = len(sh)
        for s in sh:
            index[s].add(key)

    assigned: dict[tuple[str, str, str], str] = {}
    quarantined: dict[tuple[str, str, str], str] = {}
    aborts: list[str] = []
    containment_hits: list[tuple[str, str, float]] = []

    for r in rows:
        key = (r["article_id"], r["paragraph_id"], r["digest_full"])

        # 1 — recognised: keep its recorded split, immutably. That includes a
        # recorded QUARANTINE: a paragraph awaiting adjudication stays where it
        # is until a human appends a transition. Re-running the containment check
        # on it would let a threshold change silently move it.
        if key in members:
            split = current.get(key)
            if split is None:
                raise RegistryError(
                    f"{r['paragraph_id']} is a recorded member of corpus version "
                    f"{members[key]} but no assignment event names it. The registry is "
                    f"inconsistent; do not proceed.")
            assigned[key] = split
            if split == QUARANTINE:
                quarantined[key] = "recorded quarantine, awaiting adjudication"
            continue

        # 2 — unrecognised inside an article whose whole membership is terminal.
        if r["article_id"] in closed_arts:
            aborts.append(r["paragraph_id"])
            continue

        # 3a — containment against every recorded identity, any article.
        sh = shingles(r["paragraph_text"])
        if sh:
            counts: Counter = Counter()
            for s in sh:
                for k in index.get(s, ()):
                    counts[k] += 1
            best_key, best_c = None, 0.0
            for k, n in counts.items():
                size = recorded_size.get(k, 0)
                if not size:
                    continue
                c = n / size
                if c > best_c:
                    best_key, best_c = k, c
            if best_key is not None and best_c >= CONTAINMENT_THRESHOLD:
                assigned[key] = QUARANTINE
                quarantined[key] = (
                    f"containment {best_c:.4f} >= {CONTAINMENT_THRESHOLD} against recorded "
                    f"{best_key[1]} ({reg.split_of_digest(best_key[2])})")
                containment_hits.append((r["paragraph_id"], best_key[1], best_c))
                continue

        # 3b — belt and braces: unrecognised identities in a mixed article.
        if r["article_id"] in mixed_arts:
            assigned[key] = QUARANTINE
            quarantined[key] = (
                f"unrecognised identity in mixed article {r['article_id']}, which holds both a "
                f"terminal-split and a reserve paragraph")
            continue

        # 3c — a digest shared with a different triple.
        if len(by_digest[r["digest_full"]]) > 1:
            others = [x["paragraph_id"] for x in by_digest[r["digest_full"]]
                      if x["paragraph_id"] != r["paragraph_id"]]
            assigned[key] = QUARANTINE
            quarantined[key] = (f"digest shared with {', '.join(others[:3])}; identical text, "
                                f"distinct identities -- boilerplate or a re-insertion?")
            continue

        # 4 — ordinary additive growth.
        assigned[key] = RESERVE

    if aborts:
        raise RegistryError(
            f"{len(aborts)} paragraph(s) appeared in an article whose entire membership belongs "
            f"to a terminal split: {aborts[:8]}{' ...' if len(aborts) > 8 else ''}.\n"
            f"Closed articles: {sorted(closed_arts)}. The article file was edited after its "
            f"split was coded. A new paragraph there cannot be routed by default -- it would "
            f"either join a finished set or sit next to coded text in the reserve. Adjudicate.")

    # P7: every collected paragraph gets exactly one split, quarantine included.
    # `quarantined` is now a REASON index over a subset of `assigned`, not a
    # second bucket -- which is what makes the partition trivially total instead
    # of an arithmetic coincidence two counters have to maintain.
    if len(assigned) != len(rows):
        raise RegistryError(
            f"routing assigned {len(assigned)} of {len(rows)} collected paragraphs. Every "
            f"paragraph must receive exactly one split.")
    stray = [k for k in quarantined if k not in assigned]
    if stray:
        raise RegistryError(f"{len(stray)} quarantine reason(s) name an unassigned identity: "
                            f"{[k[1] for k in stray[:5]]}")
    if sum(1 for s in assigned.values() if s == QUARANTINE) != len(quarantined):
        raise RegistryError("the quarantine split count and the reason index disagree.")

    return {
        "assigned": assigned,
        "quarantined": quarantined,
        "containment_hits": sorted(containment_hits, key=lambda x: -x[2]),
        "stats": {
            "collected": len(rows),
            "recognised": sum(1 for k in assigned if k in members),
            "new_reserve": sum(1 for k, s in assigned.items()
                               if k not in members and s == RESERVE),
            "quarantined": len(quarantined),
            "by_split": dict(Counter(assigned.values())),
            "reserve": sum(1 for s in assigned.values() if s == RESERVE),
            "closed_articles": sorted(closed_arts),
            "mixed_articles": sorted(mixed_arts),
            "containment_threshold": CONTAINMENT_THRESHOLD,
            "shingle_words": SHINGLE_WORDS,
        },
    }


DRIFT_FIELDS = ("version", "component", "key", "recorded", "observed")


def drift_fingerprint(rec: dict) -> str:
    """Stable identity of one observed drift, independent of how it is worded."""
    return hashlib.sha256(_canonical(
        {k: str(rec.get(k, "")) for k in DRIFT_FIELDS})).hexdigest()[:16]


def format_drift(rec: dict) -> str:
    return (f"{rec.get('version')}: {rec.get('component')} {rec.get('key')} "
            f"{rec.get('recorded')!r} -> {rec.get('observed')!r}")


def runtime_fingerprint() -> dict:
    """What the containment result depends on besides the text itself.

    Recorded AND verified. The Python minor version is in here because word-class
    matching in the
    normalization step resolves against the interpreter's Unicode database, so an
    upgrade can move a containment score and therefore a quarantine decision -- and
    a quarantine decision is what keeps seen text out of the reserve. Recording it
    without checking it, which is what an earlier version did, documents the
    dependency without protecting anything.
    """
    import platform
    return {
        "python": ".".join(platform.python_version_tuple()[:2]),
        "unicodedata": unicodedata.unidata_version,
        "shingle_words": SHINGLE_WORDS,
        "containment_threshold": CONTAINMENT_THRESHOLD,
        "normalization": "NFKC, casefold, non-word to space, collapse whitespace",
    }


def eligible_checksum_of(splits: dict[tuple[str, str, str], str]) -> str:
    """Checksum over the reserve's full identity TRIPLES, for the freeze event.

    Triples, not digests: hashing digests alone would let a reserve paragraph be
    relocated to another article without changing the checksum that is supposed
    to pin evaluation membership.
    """
    ids = sorted(k for k, s in splits.items() if s == RESERVE)
    return hashlib.sha256(_canonical(ids)).hexdigest()[:16]


# Kept under the old name so existing callers keep working.
eligible_checksum = eligible_checksum_of


if __name__ == "__main__":
    try:
        reg = SplitRegistry.load()
    except RegistryError as e:
        sys.exit(f"{e}")
    cur = reg.current_splits()
    print(f"schema_version {reg.schema_version}")
    print(f"corpus versions : {[v['version'] for v in reg.corpus_versions]}")
    print(f"members         : {len(reg.members())}")
    print(f"log entries     : {len(reg.assignment_log)}  (chain intact)")
    print(f"splits          : {dict(Counter(cur.values()))}")
    print(f"closed articles : {sorted(reg.closed_article_ids())}")
    print(f"mixed articles  : {sorted(reg.mixed_article_ids())}")
    fr = reg.a2_frozen()
    print(f"a2 frozen       : {'yes, seq ' + str(fr['seq']) if fr else 'no'}")
