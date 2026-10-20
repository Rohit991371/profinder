import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_scope.py"
spec = importlib.util.spec_from_file_location("check_scope", SCRIPT)
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)
C = cs.Change


def test_allowed_added_files_pass():
    assert cs.evaluate([C("A", "pipeline/profinder_pipeline/cli.py", "committed"),
                        C("A", ".github/workflows/pipeline-run.yml", "committed"),
                        C("?", "pipeline/tests/test_x.py", "worktree")]) == []


def test_outside_paths_and_non_additions_fail():
    p = cs.evaluate([C("M", "src/app/page.tsx", "committed"), C("A", ".github/workflows/ci.yml", "committed"),
                     C("D", "data/universities.json", "committed"), C("M", "pipeline/x.py", "committed"),
                     C("D", "pipeline/y.py", "committed"), C("?", "notes.txt", "worktree")])
    assert len(p) == 6


def test_forbidden_files_even_inside_pipeline():
    for path in ("pipeline/.env", "pipeline/.venv/lib/x.py", "pipeline/output/releases/a.jsonl", "pipeline/.scratch/schema.prisma",
                 "pipeline/inputs/tsinghua_list.html", "pipeline/x.db", "pipeline/dump.dump",
                 "pipeline/profinder_pipeline/__pycache__/a.pyc", "pipeline/.pytest_cache/v"):
        assert cs.evaluate([C("A", path, "committed")]), path
    for ok in ("pipeline/.env.example", "pipeline/inputs/README.md", "pipeline/inputs/.gitkeep"):
        assert cs.evaluate([C("A", ok, "committed")]) == [], ok


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("x", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "update-ref", "refs/remotes/upstream/main", "HEAD")
    git(tmp_path, "checkout", "-q", "-b", "work")


def run(tmp_path, *extra):
    return subprocess.run(["python3" if False else __import__("sys").executable, str(SCRIPT), *extra], cwd=tmp_path, capture_output=True, text=True)


def test_integration_clean_then_violations(tmp_path):
    make_repo(tmp_path)
    (tmp_path / "pipeline").mkdir()
    (tmp_path / "pipeline" / "a.py").write_text("1", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "add pipeline")
    r = run(tmp_path)
    assert r.returncode == 0 and "SCOPE OK" in r.stdout and "ADD-ONLY OK" in r.stdout, r.stdout + r.stderr
    (tmp_path / "src" / "a.ts").write_text("changed", encoding="utf-8")          # uncommitted edit of an owner file
    (tmp_path / "pipeline" / ".env").write_text("SECRET=1", encoding="utf-8")    # forbidden file
    r = run(tmp_path)
    assert r.returncode == 1 and "src/a.ts" in r.stdout and "pipeline/.env" in r.stdout
    git(tmp_path, "checkout", "--", "src/a.ts")
    (tmp_path / "pipeline" / ".env").unlink()
    git(tmp_path, "rm", "-q", "src/a.ts")
    git(tmp_path, "commit", "-qm", "delete owner file")
    r = run(tmp_path, "--no-worktree")
    assert r.returncode == 1 and "deleted file" in r.stdout or "outside allowed" in r.stdout


def test_no_base_ref_is_exit_2(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "f").write_text("x", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "c")
    r = run(tmp_path)
    assert r.returncode == 2 and "git fetch upstream" in r.stdout
