#!/usr/bin/env python3
"""Reproduction check for a copy of the repository WITHOUT its version history (submission copy, 2026-11-24).

Why this exists. The evaluator and the generators' --check modes verify the registration order through git: the
reporting threshold was sealed before the first evaluation paragraph was coded, check seals are unchanged since
their first commit, and the reference labels were committed before the run that is scored against them. A copy
without history cannot answer these questions, so `reproduce.py` refuses there. This wrapper answers them from a
RECORD of the project repository's history, `tools/runs/git_provenance.json`, written in that repository with
`--write-record`. Everything else the evaluator checks (digests of run records, manifests, labels, pins) runs
unchanged.

What the record proves and what it does not. The record states, per file, the first and the last commit (id and
committer date) and the SHA-256 of the file's content at those commits. Here the content on disk is compared with
those digests, so a file changed since the record was written is refused. The dates themselves are the project
repository's and cannot be verified from this copy; the output says so. In a repository that still has its
history, this script refuses to run: use `reproduce.py` there.

    .venv/bin/python3 tools/pipeline/snapshot_check.py                 # all generator checks, then the number tracing
    .venv/bin/python3 tools/pipeline/snapshot_check.py --run tools/pipeline/review_analyses.py --check
    .venv/bin/python3 tools/pipeline/snapshot_check.py --run tools/pipeline/evaluate.py --run-record ... --dev
    .venv/bin/python3 tools/pipeline/snapshot_check.py --write-record  # project repository only: refresh the record

Nothing here changes a result, a label or a table; it replaces three git look-ups for the duration of one process.
"""
from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RECORD = ROOT / "tools" / "runs" / "git_provenance.json"
PY = sys.executable
RECORDED = ["tools/annotation/labels", "tools/annotation/split-assignment.json", "tools/annotation/archive-manifest.json",
            "tools/runs"]
RECORDED_SKIP = ("tools/runs/tables/",)


def _git(*args: str, text: bool = True):
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=text, timeout=60)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write_record() -> None:
    """Project repository only: first and last commit of every recorded file, with the content digests."""
    head = _git("rev-parse", "HEAD").stdout.strip()
    if RECORD.exists():
        old = json.loads(RECORD.read_text(encoding="utf-8")).get("written_from_head", "")
        if _git("cat-file", "-e", old + "^{commit}").returncode != 0:
            sys.exit("REFUSING: this copy does not contain the project repository's history; the record is written there only")
    if not head or _git("status", "--porcelain", "--untracked-files=no", "--", *RECORDED).stdout.strip():
        sys.exit("REFUSING: the recorded paths have uncommitted changes (or this is not a git repository)")
    files = [f for f in _git("ls-files", "--", *RECORDED).stdout.splitlines()
             if not f.startswith(RECORDED_SKIP) and f != str(RECORD.relative_to(ROOT))]
    out = {}
    for rel in files:
        first = _git("log", "--diff-filter=A", "--format=%H %cI", "--follow", "--", rel).stdout.strip().splitlines()
        last = _git("log", "-1", "--format=%H %cI", "--", rel).stdout.strip()
        if not first or not last:
            continue
        fc, fd = first[-1].split(" ", 1)
        lc, ld = last.split(" ", 1)
        fb = _git("show", f"{fc}:{rel}", text=False)
        hb = _git("show", f"HEAD:{rel}", text=False)
        out[rel] = {"first": {"commit": fc, "committed_at": fd, "sha256": _sha(fb.stdout) if fb.returncode == 0 else None},
                    "last": {"commit": lc, "committed_at": ld, "sha256": _sha(hb.stdout) if hb.returncode == 0 else None}}
    doc = {"kind": "git_provenance_record",
           "note": "First and last commit of each file in the project repository, with the SHA-256 of its content at "
                   "those commits. Read by tools/pipeline/snapshot_check.py in a copy without version history; the "
                   "dates are the project repository's and are not verifiable from such a copy.",
           "written_from_head": head, "files": out}
    RECORD.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {RECORD.relative_to(ROOT)}: {len(out)} files, head {head[:12]} (commit the record itself afterwards)")


def load_record() -> dict:
    if not RECORD.exists():
        sys.exit(f"REFUSING: {RECORD.relative_to(ROOT)} is missing")
    doc = json.loads(RECORD.read_text(encoding="utf-8"))
    if _git("cat-file", "-e", doc["written_from_head"] + "^{commit}").returncode == 0:
        sys.exit("REFUSING: this repository has its version history; run tools/pipeline/reproduce.py instead")
    return doc


def install(doc: dict) -> None:
    """Replace the three git look-ups by the record, for this process."""
    sys.path.insert(0, str(HERE))
    import evaluate as ev
    import sample_contract as sc
    files = doc["files"]

    def entry(path: Path, what: str) -> tuple[str, dict]:
        try:
            rel = str(Path(path).resolve().relative_to(ROOT.resolve()))
        except ValueError:
            raise sc.ContractError(f"{what} at {path} lies outside the repository") from None
        if rel not in files:
            raise sc.ContractError(f"{what} {rel} has no entry in the provenance record (not committed in the project repository)")
        return rel, files[rel]

    def check(path: Path, rel: str, rec: dict, which: str, what: str) -> dict:
        if rec[which]["sha256"] != _sha(Path(path).read_bytes()):
            raise sc.ContractError(f"{what} {rel} differs from its recorded {'first commit' if which == 'first' else 'last committed version'}")
        return {"commit": rec[which]["commit"], "committed_at": rec[which]["committed_at"]}

    def seal_commitment(seal_path: Path) -> dict:
        rel, rec = entry(seal_path, "the threshold seal")
        return check(seal_path, rel, rec, "first", "the threshold seal")

    def file_commitment(path: Path, what: str, *, first: bool) -> dict:
        rel, rec = entry(path, what)
        return check(path, rel, rec, "first" if first else "last", what)

    def git_last_change(path: Path):
        try:
            rel = str(Path(path).resolve().relative_to(ROOT.resolve()))
        except ValueError:
            return None, None
        rec = files.get(rel)
        return (rec["last"]["committed_at"], rec["last"]["sha256"]) if rec else (None, None)

    sc._seal_commitment = seal_commitment
    sc.file_commitment = file_commitment
    ev.GIT_LAST_CHANGE = git_last_change


def run_one(argv: list[str]) -> None:
    doc = load_record()
    install(doc)
    print(f"SNAPSHOT MODE: commit order read from {RECORD.relative_to(ROOT)} (project repository at "
          f"{doc['written_from_head'][:12]}); not verified by this copy's own history.", file=sys.stderr)
    script = (ROOT / argv[0]).resolve()
    sys.argv = [str(script)] + argv[1:]
    if script.name == "evaluate.py":                       # run as the imported (patched) module, not as a second copy
        import evaluate as ev
        ev.main()
        return
    runpy.run_path(str(script), run_name="__main__")


def run_all() -> None:
    doc = load_record()
    sys.path.insert(0, str(HERE))
    import reproduce as rp
    print(f"SNAPSHOT MODE: commit order read from {RECORD.relative_to(ROOT)} (project repository at "
          f"{doc['written_from_head'][:12]}); not verified by this copy's own history.")
    print("(1) generated floats against the run records")
    ok = True
    me = str(Path(__file__).resolve())
    for cmd in rp.GENERATORS:
        r = subprocess.run([PY, me, "--run", *cmd[1:]], cwd=ROOT, capture_output=True, text=True)
        good = r.returncode == 0 and "STALE" not in (r.stdout + r.stderr)
        ok &= good
        print(f"  {'ok  ' if good else 'FAIL'} {' '.join(Path(c).name if i == 0 else c for i, c in enumerate(cmd[1:4]))}")
        if not good:
            print("       " + (r.stdout + r.stderr).strip().replace("\n", "\n       ")[-800:])
    st = _git("status", "--porcelain", "paper/")
    if st.returncode != 0:
        ok = False
        print("  FAIL re-render comparison not run: this folder is not a git repository (run `git init`, add and commit first)")
    elif st.stdout.strip():
        ok = False
        print("  FAIL re-render comparison not run: paper/ has uncommitted changes (commit or restore them first)")
    else:
        failed = []
        for cmd in rp.RERENDER:
            if cmd[1].endswith("codebook_review.py") and not (ROOT / "tools/runs/raw/codebook_review/packet_key.json").exists():
                print("  skip codebook_review.py score: it reads the withheld raw judge files (tools/runs/raw/); the "
                      "codebook-review table is not re-rendered in this copy")
                continue
            r = subprocess.run([PY, *cmd[1:]], cwd=ROOT, capture_output=True, text=True)
            if r.returncode != 0:
                failed.append(" ".join(cmd[1:3]) + ": " + (r.stdout + r.stderr).strip()[-200:])
        changed = _git("status", "--porcelain", "paper/").stdout.splitlines()
        good = not failed and not changed
        ok &= good
        print(f"  {'ok  ' if good else 'FAIL'} re-render of figures.py codebook/tables/results and codebook_review score"
              + ("" if good else f": failed {failed}; changed {changed}"))
    r = subprocess.run([PY, str(HERE / "reproduce.py"), "--skip-generators"], cwd=ROOT, capture_output=True, text=True)
    print(r.stdout.rstrip())
    sys.exit(0 if ok and r.returncode == 0 else 1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--write-record":
        write_record()
    elif len(sys.argv) > 2 and sys.argv[1] == "--run":
        run_one(sys.argv[2:])
    elif len(sys.argv) == 1:
        run_all()
    else:
        sys.exit(__doc__)
