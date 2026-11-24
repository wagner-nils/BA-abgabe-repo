#!/usr/bin/env python3
"""Read the frozen codebook's value lists, so nothing hardcodes a second copy.

tools/docs/PROTOTYPE-PLAN.md section A.6: the codebook is the single source of the prompt, the JSON
schema and the annotation sheet. A validator carrying its own copy of the
technology list is a fourth source that drifts silently -- which is the failure
mode section A.6 exists to prevent, so the validator reads the codebook too.

`pyyaml` is not installed and the brief forbids installing packages without
asking, so this parses the specific shapes the codebook uses rather than pretending
to be a YAML parser. It is deliberately strict: an unrecognised shape raises
instead of returning a partial list, because a validator that silently sees
"0 permitted technologies" would accept everything.

    python tools/annotation/codebook_reader.py     # print what it sees
"""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODEBOOK_DIR = HERE / "codebook"


@dataclass
class Codebook:
    path: Path
    version: str
    sha256: str
    technologies: list[str]
    frame_elements: list[str]
    frame_derivation: dict[str, list[str]]   # frame name -> element ids
    evaluations: list[str]
    comparison_targets: list[str]
    speakers: list[str]
    confidences: list[str]
    subtypes: dict[str, list[str]]           # column name -> permitted values

    @property
    def eval_column_for(self) -> dict[str, str]:
        """element id -> the eval_* column that must be filled when it fires."""
        return {el: f"eval_{frame}"
                for frame, els in self.frame_derivation.items() for el in els}


def _section(text: str, name: str) -> str:
    """The block from `name:` at column 0 up to the next column-0 key."""
    m = re.search(rf"^{re.escape(name)}:.*?(?=^\S)", text, re.M | re.S)
    if not m:
        raise ValueError(f"codebook has no top-level `{name}:` section")
    return m.group(0)


def _ids(block: str, *, depth: int = 2) -> list[str]:
    """`- id: foo` entries at exactly `depth` spaces of indentation, in order.

    Depth matters: a future nested `- id:` list (aliases, excluded values,
    worked examples) would otherwise be absorbed into the enclosing enum, and
    the result would look like a longer valid-value list rather than an error.

    Duplicates RAISE rather than being silently collapsed -- a duplicated id in
    the instrument is a defect in the instrument, and quietly deduplicating it
    means the codebook and this reader disagree about how many values exist.
    """
    found = re.findall(rf"^ {{{depth}}}-\s*id:\s*\"?([A-Za-z0-9_]+)\"?", block, re.M)
    dupes = sorted({v for v in found if found.count(v) > 1})
    if dupes:
        raise ValueError(f"duplicate id(s) {dupes} in a codebook value list. Two entries with "
                         f"one id cannot both be the permitted value.")
    return found


def _inline_list(block: str, key: str) -> list[str]:
    """`key: [a, b,\n       c]` -- possibly spanning lines."""
    m = re.search(rf"^\s*{re.escape(key)}:\s*\[(.*?)\]", block, re.M | re.S)
    if not m:
        raise ValueError(f"no inline list `{key}:` found")
    body = m.group(1)
    # Strip trailing comments per line; a `#` inside the list would otherwise
    # become part of a value.
    body = "\n".join(line.split("#", 1)[0] for line in body.splitlines())
    vals = [v.strip().strip('"\'') for v in body.split(",") if v.strip()]
    dupes = sorted({v for v in vals if vals.count(v) > 1})
    if dupes:
        raise ValueError(f"inline list `{key}` has duplicate value(s) {dupes}")
    if any(not re.fullmatch(r"[A-Za-z0-9_]+", v) for v in vals):
        raise ValueError(f"inline list `{key}` has a value this reader cannot trust "
                         f"(quoting or nesting it does not handle): {vals}")
    return vals


def _version_key(v: str) -> tuple[int, ...]:
    """Numeric, so v1.10 sorts after v1.9 rather than before it."""
    return tuple(int(x) for x in v.split("."))


def load_version(version: str) -> Codebook:
    """The codebook for a specific version -- e.g. "0.3".

    Re-finalizing a historical session has to validate against the instrument that
    session was coded under, not against the current frozen one. A v0.3 sheet has
    16 elements; F2c arrived in v1.0. Validating it against v1.0's element list
    refuses a correct file, and the versioned-labels design (labels/A1_v1.csv,
    A1_v2.csv, never overwritten) depends on old sessions staying processable.
    """
    for f in CODEBOOK_DIR.glob("codebook-v*.yaml"):
        m = re.search(r'^version:\s*"?([\d.]+)"?', f.read_text(encoding="utf-8"), re.M)
        if m and m.group(1) == version:
            return load(f)
    raise ValueError(f"no codebook file declares version {version!r} in {CODEBOOK_DIR}")


def load(path: Path | None = None) -> Codebook:
    """The newest codebook marked `frozen: true`, parsed. Raises if none is."""
    if path is None:
        frozen = []
        for f in CODEBOOK_DIR.glob("codebook-v*.yaml"):
            text = f.read_text(encoding="utf-8")
            m = re.search(r'^version:\s*"?([\d.]+)"?', text, re.M)
            if m and re.search(r"^frozen:\s*true\s*$", text, re.M):
                frozen.append((_version_key(m.group(1)), f))
        if not frozen:
            raise ValueError(f"no codebook in {CODEBOOK_DIR} is marked `frozen: true`")
        # numeric, not lexicographic: sorted() would put v1.10 before v1.9
        path = max(frozen)[1]

    text = path.read_text(encoding="utf-8")
    vm = re.search(r'^version:\s*"?([\d.]+)"?', text, re.M)
    if not vm:
        raise ValueError(f"{path.name} has no `version:`")

    tech_block = _section(text, "technologies")
    technologies = _ids(tech_block)
    frame_elements = _ids(_section(text, "frame_elements"))

    # Subtype columns. Their permitted values live in a trailing YAML COMMENT in
    # v1.0 -- `subtype_field: wind_siting   # onshore | offshore | unspecified` --
    # which is the only place they exist, so that is what is parsed. Fragile by
    # nature: a reworded comment silently changes the permitted set, so a
    # malformed one raises rather than yielding a short list.
    subtypes: dict[str, list[str]] = {}
    for col, comment in re.findall(r"^\s*subtype_field:\s*([A-Za-z0-9_]+)\s*#\s*(.+)$",
                                   tech_block, re.M):
        vals = [v.strip() for v in comment.split("|")]
        if len(vals) < 2 or any(not re.fullmatch(r"[a-z_]+", v) for v in vals):
            raise ValueError(
                f"subtype_field `{col}` declares its permitted values in a comment this "
                f"reader cannot parse: {comment!r}. Expected `a | b | c`. Fix the comment "
                f"or move the values into real YAML -- a subtype whose values are unknown "
                f"cannot be validated, and treating it as binary rejects every correct value.")
        subtypes[col] = vals

    deriv_block = _section(text, "frame_derivation")
    frame_derivation = {}
    for name, els in re.findall(r"^\s{2}([a-z_]+):\s*\[([^\]]*)\]", deriv_block, re.M):
        if name in frame_derivation:
            raise ValueError(f"`frame_derivation` declares frame {name!r} twice; the second "
                             f"would silently overwrite the first.")
        frame_derivation[name] = [e.strip() for e in els.split(",") if e.strip()]

    ev_block = _section(text, "evaluation")
    evaluations = _ids(ev_block, depth=4)          # nested under `values:`
    comparison_targets = _inline_list(_section(text, "comparison_target"), "values")
    speakers = _ids(_section(text, "speaker"), depth=4)   # nested under `values:`
    conf_block = _section(text, "confidence")
    confidences = [c for c in re.findall(r"\{\s*id:\s*(\d+)", conf_block)]

    cb = Codebook(
        path=path,
        version=vm.group(1),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        technologies=technologies,
        frame_elements=frame_elements,
        frame_derivation=frame_derivation,
        evaluations=evaluations,
        comparison_targets=comparison_targets,
        speakers=speakers,
        confidences=confidences,
        subtypes=subtypes,
    )

    # These floors exist to catch a PARSE FAILURE -- an empty or truncated list --
    # not to assert how large the instrument is. A validator that believes nothing
    # is permitted accepts everything, because every membership test runs against
    # an empty set. They are therefore set below the smallest real version (v0.1:
    # 7 technologies, 11 elements, 6 frames) rather than at the current version's
    # size: raising them to v1.0's numbers made every historical codebook
    # unreadable, and re-finalizing a v0.3 session is something the versioned-labels
    # design requires. The real structural guarantee is the partition check below.
    for name, got, at_least in (("technologies", technologies, 5),
                                ("frame_elements", frame_elements, 8),
                                ("frame_derivation", frame_derivation, 5),
                                ("evaluations", evaluations, 3),
                                ("comparison_targets", comparison_targets, 5),
                                ("speakers", speakers, 4),
                                ("confidences", confidences, 3),
                                ("subtypes", subtypes, 2)):
        if len(got) < at_least:
            raise ValueError(
                f"{path.name}: parsed only {len(got)} {name} (floor {at_least}, set below the "
                f"smallest real codebook version). This is a parse failure, not a small "
                f"codebook -- fix the reader rather than lowering the floor, because a short "
                f"list here silently weakens every validation downstream. Got: {got}")

    # The derivation must be a PARTITION of the element set: every element in
    # exactly one frame, no frame naming an element that does not exist. A
    # minimum count cannot catch an element assigned to two frames or a
    # derivation entry pointing at a typo.
    flat = [e for els in frame_derivation.values() for e in els]
    twice = sorted({e for e in flat if flat.count(e) > 1})
    if twice:
        raise ValueError(f"{path.name}: element(s) {twice} appear in more than one "
                         f"`frame_derivation` frame, so `eval_*` for them is ambiguous.")
    missing = [e for e in frame_elements if e not in flat]
    if missing:
        raise ValueError(f"{path.name}: element(s) {missing} are in `frame_elements` but in no "
                         f"`frame_derivation` frame, so no evaluation column covers them.")
    unknown = [e for e in flat if e not in frame_elements]
    if unknown:
        raise ValueError(f"{path.name}: `frame_derivation` names element(s) {unknown} that are "
                         f"not in `frame_elements` -- a typo or a stale entry.")
    return cb


def main() -> None:
    cb = load()
    print(f"{cb.path.name}  v{cb.version}  sha256={cb.sha256[:16]}")
    print(f"  technologies       {cb.technologies}")
    print(f"  frame elements     {len(cb.frame_elements)}: {cb.frame_elements}")
    print(f"  frames             { dict((k, len(v)) for k, v in cb.frame_derivation.items()) }")
    print(f"  evaluations        {cb.evaluations}")
    print(f"  comparison targets {cb.comparison_targets}")
    print(f"  speakers           {cb.speakers}")
    print(f"  confidence         {cb.confidences}")
    print(f"  subtypes           {cb.subtypes}")


if __name__ == "__main__":
    sys.exit(main())
