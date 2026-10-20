"""`app-schema`: create the app tables in the LOCAL Docker Postgres from a scratch copy of prisma/schema.prisma.
prisma/schema.prisma is never edited; the provider is flipped only in pipeline/.scratch/ (git-ignored)."""
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from ..logging_setup import redact
from ..paths import PRISMA_SCHEMA, SCRATCH_DIR
from .hostguard import require_local
from .prisma_schema import parse_models, to_postgres_schema

PRISMA_VERSION = "5.22.0"   # pinned: `npx prisma@5.22.0` avoids npm install and any lockfile change
SCRATCH_SCHEMA = SCRATCH_DIR / "schema.postgres.prisma"


def write_scratch_schema(source: Path = PRISMA_SCHEMA, target: Path = SCRATCH_SCHEMA) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_postgres_schema(source.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    return target


def run_db_push(app_database_url: str, schema_path: Path = SCRATCH_SCHEMA) -> int:
    """`npx prisma@5.22.0 db push --schema <scratch> --skip-generate` against the local DB. No --accept-data-loss, ever."""
    npx = shutil.which("npx")
    if not npx:
        raise RuntimeError("npx not found; install Node.js 20 LTS")
    env = {**os.environ, "DATABASE_URL": app_database_url, "CHECKPOINT_DISABLE": "1", "PRISMA_HIDE_UPDATE_MESSAGE": "1"}
    cmd = [npx, "--yes", f"prisma@{PRISMA_VERSION}", "db", "push", "--schema", str(schema_path), "--skip-generate"]
    proc = subprocess.run(cmd, cwd=str(SCRATCH_DIR), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(redact(proc.stdout + proc.stderr))
    return proc.returncode


def app_schema(app_database_url: Optional[str], dry_run: bool = False) -> int:
    if not app_database_url:
        raise SystemExit("APP_DATABASE_URL is not set in pipeline/.env")
    host = require_local(app_database_url, "app-schema")
    path = write_scratch_schema()
    expected = sorted(parse_models(path.read_text(encoding="utf-8")))
    print(f"scratch schema written: {path.relative_to(path.parents[1])} ({len(expected)} models)")
    if dry_run:
        print(f"dry run: would run prisma@{PRISMA_VERSION} db push against {host}")
        return 0
    print(f"running prisma@{PRISMA_VERSION} db push against {host} ...")
    code = run_db_push(app_database_url)
    if code != 0:
        print("prisma db push FAILED (exit code %d). Paste the lines above back to Claude (they contain no secrets)." % code)
        return code
    from .connection import connect  # noqa: PLC0415
    with connect(app_database_url) as conn:
        have = {r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()}
    missing = [m for m in expected if m not in have]
    print(f"tables in public: {len(have & set(expected))}/{len(expected)} expected models present")
    if missing:
        print("MISSING tables:", ", ".join(missing))
        return 1
    print("app-schema OK")
    return 0
