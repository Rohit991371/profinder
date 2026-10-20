"""Tiny SQL migration runner for the `ingest` schema. Files: migrations/NNN_name.sql, applied once, in order.
The checksum ignores CRLF/LF so Windows and Linux checkouts agree."""
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..paths import MIGRATIONS_DIR

_NAME_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")
BOOTSTRAP_SQL = """
CREATE SCHEMA IF NOT EXISTS ingest;
CREATE TABLE IF NOT EXISTS ingest.migration (
    version    text PRIMARY KEY,
    checksum   char(64) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);
"""


class MigrationDrift(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    checksum: str


def sql_checksum(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def list_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    out: list[Migration] = []
    for p in sorted(directory.glob("*.sql")):
        if not _NAME_RE.match(p.name):
            raise ValueError(f"migration file name must look like 001_name.sql: {p.name}")
        out.append(Migration(p.stem, p, sql_checksum(p.read_text(encoding="utf-8"))))
    versions = [m.version[:3] for m in out]
    if len(set(versions)) != len(versions):
        raise ValueError("duplicate migration numbers")
    return out


def apply_migrations(conn: Any, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations; return the versions applied. Raises MigrationDrift if an applied file was edited."""
    conn.execute(BOOTSTRAP_SQL)
    applied = {row[0]: row[1].strip() for row in conn.execute("SELECT version, checksum FROM ingest.migration").fetchall()}
    done: list[str] = []
    for m in list_migrations(directory):
        if m.version in applied:
            if applied[m.version] != m.checksum:
                raise MigrationDrift(f"{m.version} was edited after it was applied; add a new migration instead")
            continue
        with conn.transaction():
            conn.execute(m.path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO ingest.migration (version, checksum) VALUES (%s, %s)", (m.version, m.checksum))
        done.append(m.version)
    conn.commit()
    return done


EXPECTED_TABLES = ("run", "target", "page", "candidate", "release")


def ingest_tables(conn: Any) -> list[str]:
    rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'ingest' ORDER BY 1").fetchall()
    return [r[0] for r in rows]
