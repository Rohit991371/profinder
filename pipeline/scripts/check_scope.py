#!/usr/bin/env python3
"""Scope guard (decision D2). The pipeline ADDS files only, and only under:
    pipeline/**                      and        .github/workflows/pipeline-*.yml
Checks (a) the commits since the owner's main (add-only, allowed paths) and (b) uncommitted/untracked work.
Also refuses files that must never be committed (secrets, databases, dumps, caches).

Usage (any directory inside the repo):  python pipeline/scripts/check_scope.py [--base upstream/main] [--no-worktree]
Exit code 0 = SCOPE OK / ADD-ONLY OK; 1 = violations; 2 = could not run. Stdlib only (works without the venv).
"""
import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Optional

ALLOWED_RE = re.compile(r"^(pipeline/|\.github/workflows/pipeline-[^/]+\.ya?ml$)")
# Never commit (INSTRUCTIONS Part B). Matched against the path relative to the repo root.
FORBIDDEN_RES = [
    (re.compile(r"^pipeline/\.env$"), "secrets file .env"),
    (re.compile(r"^pipeline/\.venv/"), "virtualenv"),
    (re.compile(r"^pipeline/(output|state|\.scratch)/"), "generated output / scratch"),
    (re.compile(r"^pipeline/inputs/(?!README\.md$|\.gitkeep$)"), "input data file (inputs/ is git-ignored)"),
    (re.compile(r"\.(db|sqlite|sqlite3|dump|bak)$", re.I), "database file or dump"),
    (re.compile(r"(^|/)(__pycache__|\.pytest_cache)/"), "Python cache"),
    (re.compile(r"\.pyc$"), "Python bytecode"),
]


@dataclass(frozen=True)
class Change:
    status: str      # A added, M modified, D deleted, R renamed/copied, ? untracked, U other
    path: str
    origin: str      # "committed" or "worktree"


def evaluate(changes: list[Change]) -> list[str]:
    """Pure function: list of human-readable violations (empty = clean)."""
    problems: list[str] = []
    for c in changes:
        path = c.path.replace("\\", "/")
        if not ALLOWED_RE.match(path):
            problems.append(f"[{c.origin}] outside allowed paths: {c.status} {path}")
            continue
        if c.status == "D" and c.origin == "committed":
            problems.append(f"[{c.origin}] deleted file: {path}")
        elif c.status in ("M", "R", "U") and c.origin == "committed":
            problems.append(f"[{c.origin}] existing file changed (add-only rule): {c.status} {path}")
        if c.status != "D":
            for rx, why in FORBIDDEN_RES:
                if rx.search(path):
                    problems.append(f"[{c.origin}] must never be committed ({why}): {path}")
                    break
    return problems


def _git(args: list[str], cwd: Optional[str] = None) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def repo_root(start: Optional[str] = None) -> str:
    return _git(["rev-parse", "--show-toplevel"], cwd=start).strip()


def resolve_base(root: str, base: Optional[str]) -> str:
    for cand in ([base] if base else []) + ["upstream/main", "origin/main"]:
        try:
            _git(["rev-parse", "--verify", "--quiet", cand], cwd=root)
            return cand
        except RuntimeError:
            continue
    raise RuntimeError("no base ref found. Run: git fetch upstream   (the owner's repo must be the 'upstream' remote)")


def committed_changes(root: str, base: str) -> list[Change]:
    out = _git(["diff", "--name-status", "--no-renames", "-z", f"{base}...HEAD"], cwd=root)
    parts = [p for p in out.split("\0") if p]
    return [Change(parts[i][0], parts[i + 1], "committed") for i in range(0, len(parts) - 1, 2)]


def worktree_changes(root: str) -> list[Change]:
    out = _git(["status", "--porcelain=v1", "-z", "-uall"], cwd=root)
    entries = [e for e in out.split("\0") if e]
    changes: list[Change] = []
    i = 0
    while i < len(entries):
        e = entries[i]
        xy, path = e[:2], e[3:]
        if "R" in xy or "C" in xy:           # rename/copy: next entry is the original path
            i += 1
            changes.append(Change("R", entries[i] if i < len(entries) else path, "worktree"))
        if xy == "??":
            status = "?"
        elif "D" in xy:
            status = "D"
        elif "A" in xy:
            status = "A"
        else:
            status = "M"
        changes.append(Change(status, path, "worktree"))
        i += 1
    return changes


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", help="ref to compare with (default: upstream/main, then origin/main)")
    ap.add_argument("--no-worktree", action="store_true", help="skip the uncommitted/untracked check")
    args = ap.parse_args(argv)
    try:
        root = repo_root()
        base = resolve_base(root, args.base)
        changes = committed_changes(root, base)
        if not args.no_worktree:
            changes += worktree_changes(root)
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"check_scope could not run: {exc}")
        return 2
    problems = evaluate(changes)
    n_files = len({PurePosixPath(c.path).as_posix() for c in changes})
    if problems:
        groups: dict[str, list[str]] = {}
        for p in problems:
            head, _, tail = p.rpartition(": ")
            groups.setdefault(head or p, []).append(tail)
        print(f"SCOPE VIOLATIONS ({len(problems)}), base {base}:")
        for head, paths in groups.items():
            print(f"  - {head}: {len(paths)} file(s), e.g. {', '.join(paths[:3])}")
        if any("Python cache" in h or "Python bytecode" in h for h in groups):
            print("Hint: add these lines to pipeline/.gitignore:  __pycache__/  .pytest_cache/  *.pyc")
        print("Fix: `git checkout -- <file>` for modified repo files; remove or git-ignore the others. Tell Claude if unsure.")
        return 1
    print(f"SCOPE OK (base {base}, {n_files} changed paths, all inside pipeline/ or .github/workflows/pipeline-*.yml)")
    print("ADD-ONLY OK (no existing file modified or deleted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
