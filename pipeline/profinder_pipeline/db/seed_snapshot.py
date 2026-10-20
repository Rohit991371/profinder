"""`seed-snapshot`: copy CONTENT tables only from prisma/dev.db into the local Docker Postgres.

Safety (doc 03 section 2.2):
  * dev.db is opened read-only+immutable and only the whitelisted CONTENT_TABLES are ever queried
    (the Student / Application / ... tables with password hashes are never read);
  * the target must be localhost; the whole copy is one transaction and is rolled back unless counts match;
  * columns are matched by name, so dev.db being older than schema.prisma is fine (new columns use their defaults).
"""
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from ..paths import DEV_DB
from .hostguard import require_local

# Parents before children (foreign keys).
CONTENT_TABLES = ("University", "ResearchArea", "Topic", "Program", "Professor", "Scholarship", "Publication",
                  "ProfessorSource", "ProfessorResearchArea", "ProfessorTopic")
# Tables that must stay empty in the fork DB.
USER_TABLES = ("Student", "Application", "SavedProfessor", "Subscription", "UsageCounter", "BillingEvent",
               "ApplyRequest", "ApplyRequestItem", "ApplyDocument", "MatchFeedback")
# Counts recorded in the planning docs (informational; the hard check is source == target).
DOCUMENTED_COUNTS = {"University": 104, "ResearchArea": 24, "Topic": 1318, "Program": 320, "Professor": 2306,
                     "Scholarship": 220, "Publication": 7111}
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BATCH = 1000


def ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"unsafe identifier {name!r}")
    return f'"{name}"'


@dataclass(frozen=True)
class TargetColumn:
    name: str
    pg_type: str
    nullable: bool
    has_default: bool


def _to_naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def convert_value(value: Any, pg_type: str) -> Any:
    """SQLite value -> value for a Postgres column of `pg_type` (information_schema.data_type)."""
    if value is None:
        return None
    if pg_type.startswith("timestamp"):
        if isinstance(value, (int, float)):
            seconds = value / 1000 if abs(value) > 1e11 else value      # Prisma/SQLite stores ms since epoch
            return datetime(1970, 1, 1) + timedelta(seconds=seconds)
        text = str(value).strip().replace("Z", "+00:00")
        if re.fullmatch(r"-?\d+(\.\d+)?", text):
            return convert_value(float(text), pg_type)
        return _to_naive_utc(datetime.fromisoformat(text))
    if pg_type == "boolean":
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("1", "true", "t", "yes"):
                return True
            if low in ("0", "false", "f", "no", ""):
                return False
            raise ValueError(f"cannot read boolean from {value!r}")
        return bool(value)
    if pg_type in ("integer", "bigint", "smallint"):
        return int(value)
    if pg_type in ("double precision", "real", "numeric"):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else str(value)


def plan_columns(source_cols: list[str], target_cols: list[TargetColumn]) -> tuple[list[TargetColumn], list[str]]:
    """-> (columns to copy, problems). A problem is a NOT NULL target column with no default that dev.db lacks."""
    src = set(source_cols)
    copy = [c for c in target_cols if c.name in src]
    problems = [c.name for c in target_cols if c.name not in src and not c.nullable and not c.has_default]
    return copy, problems


def open_dev_db(path: Path = DEV_DB) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"{path} not found")
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)


def _target_columns(conn: Any, table: str) -> list[TargetColumn]:
    rows = conn.execute(
        "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (table,)).fetchall()
    return [TargetColumn(r[0], r[1], r[2] == "YES", r[3] is not None) for r in rows]


def _count(conn: Any, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM public.{ident(table)}").fetchone()[0]


def seed_snapshot(app_database_url: Optional[str], source: Path = DEV_DB, replace: bool = False, dry_run: bool = False) -> int:
    if not app_database_url:
        raise SystemExit("APP_DATABASE_URL is not set in pipeline/.env")
    host = require_local(app_database_url, "seed-snapshot")
    from .connection import connect  # noqa: PLC0415

    sq = open_dev_db(source)
    try:
        src_counts = {t: sq.execute(f"SELECT count(*) FROM {ident(t)}").fetchone()[0] for t in CONTENT_TABLES}
    except sqlite3.OperationalError as exc:
        print(f"dev.db is missing an expected content table ({exc})")
        return 1
    print(f"source: {source.name} (read-only) | target: {host}")

    with connect(app_database_url) as conn:
        missing_tables = [t for t in CONTENT_TABLES if not _target_columns(conn, t)]
        if missing_tables:
            print("target tables missing:", ", ".join(missing_tables), "- run `app-schema` first")
            return 1
        user_rows = {t: _count(conn, t) for t in USER_TABLES if _target_columns(conn, t)}
        if any(user_rows.values()):
            print("REFUSED: user tables are not empty in the target:", {k: v for k, v in user_rows.items() if v})
            return 1
        existing = {t: _count(conn, t) for t in CONTENT_TABLES}
        if any(existing.values()):
            if existing == src_counts:
                print("already seeded (counts equal the source); nothing to do")
                return _report(src_counts, existing, user_rows)
            if not replace:
                print("target content tables are not empty:", {k: v for k, v in existing.items() if v})
                print("re-run with --replace to TRUNCATE the 10 content tables (local throwaway DB only)")
                return 1

        plans: dict[str, tuple[list[TargetColumn], list[str]]] = {}
        for t in CONTENT_TABLES:
            src_cols = [r[1] for r in sq.execute(f"PRAGMA table_info({ident(t)})").fetchall()]
            if not src_cols:
                print(f"source table {t} not found in dev.db")
                return 1
            plans[t] = plan_columns(src_cols, _target_columns(conn, t))
        bad = {t: p[1] for t, p in plans.items() if p[1]}
        if bad:
            print("cannot copy: NOT NULL columns without a default are missing in dev.db:", bad)
            return 1
        if dry_run:
            for t in CONTENT_TABLES:
                print(f"  {t}: would copy {src_counts[t]} rows, {len(plans[t][0])} columns")
            print("dry run: nothing written")
            return 0

        with conn.transaction():
            if any(existing.values()):
                conn.execute("TRUNCATE " + ", ".join(f"public.{ident(t)}" for t in CONTENT_TABLES) + " CASCADE")
            for t in CONTENT_TABLES:
                cols = plans[t][0]
                col_sql = ", ".join(ident(c.name) for c in cols)
                insert = f"INSERT INTO public.{ident(t)} ({col_sql}) VALUES ({', '.join(['%s'] * len(cols))})"
                cur = sq.execute(f"SELECT {col_sql} FROM {ident(t)}")
                while True:
                    rows = cur.fetchmany(BATCH)
                    if not rows:
                        break
                    data = [tuple(convert_value(v, c.pg_type) for v, c in zip(row, cols)) for row in rows]
                    with conn.cursor() as cc:
                        cc.executemany(insert, data)
            got = {t: _count(conn, t) for t in CONTENT_TABLES}
            if got != src_counts:
                raise RuntimeError(f"count mismatch, rolling back: source {src_counts} target {got}")
        user_rows = {t: _count(conn, t) for t in USER_TABLES if _target_columns(conn, t)}
        return _report(src_counts, {t: _count(conn, t) for t in CONTENT_TABLES}, user_rows)


def _report(src: dict[str, int], tgt: dict[str, int], user_rows: dict[str, int]) -> int:
    print(f"{'table':<24}{'source':>8}{'target':>8}  documented")
    for t in CONTENT_TABLES:
        doc = DOCUMENTED_COUNTS.get(t)
        flag = "" if doc is None else ("ok" if doc == tgt[t] else f"(docs said {doc})")
        print(f"{t:<24}{src[t]:>8}{tgt[t]:>8}  {flag}")
    ok = src == tgt and not any(user_rows.values())
    print("user tables rows:", sum(user_rows.values()), "(must be 0)")
    print("seed-snapshot OK" if ok else "seed-snapshot MISMATCH")
    return 0 if ok else 1
