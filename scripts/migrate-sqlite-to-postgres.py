#!/usr/bin/env python3
"""Idempotent SQLite → PostgreSQL recovery for ProFinderNow.

Preserves primary keys. Never drops, truncates, or overwrites existing rows.
Conflicts are reported, not silently swallowed.

Required:
  DATABASE_URL          postgres connection string (recovery branch only)
  CONFIRM_MIGRATE=preserve-ids   for actual writes
  SQLITE_PATH           default prisma/dev.db

Optional:
  DRY_RUN=1             report only, no writes

Refuses known production hosts and any host that is not the verified recovery host
unless ALLOW_HOST_OVERRIDE=1 is set (never use on production).
"""
import os
import sqlite3
import sys

CONFIRM = os.environ.get("CONFIRM_MIGRATE")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
SQLITE_PATH = os.environ.get("SQLITE_PATH", "prisma/dev.db")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

VERIFIED_RECOVERY_HOST = "ep-broad-bird-b4qa54ze-pooler.c-6.us-east-2.aws.neon.tech"
FORBIDDEN_HOST_FRAGMENTS = ("prod", "production")

if not DRY_RUN and CONFIRM != "preserve-ids":
    sys.exit("Refusing to migrate. Set CONFIRM_MIGRATE=preserve-ids or DRY_RUN=1.")
if not DATABASE_URL.startswith("postgres"):
    sys.exit("DATABASE_URL must be a PostgreSQL URL. Value not printed.")
if DATABASE_URL.startswith("file:"):
    sys.exit("Refusing file: target.")

from urllib.parse import urlparse
host = urlparse(DATABASE_URL).hostname or ""
if any(f in host.lower() for f in FORBIDDEN_HOST_FRAGMENTS):
    sys.exit("Refusing host that looks like production. Value not printed.")
if host != VERIFIED_RECOVERY_HOST and os.environ.get("ALLOW_HOST_OVERRIDE") != "1":
    sys.exit(f"Host {host} is not the verified recovery host. Aborting.")

try:
    import psycopg
except ImportError:
    sys.exit("Install psycopg (pip install psycopg[binary]) before running. No rows changed.")

TABLES = [
    "University", "ResearchArea", "Topic", "Program", "Professor", "Scholarship",
    "Student", "Application", "SavedProfessor", "Subscription", "UsageCounter",
    "BillingEvent", "Publication", "ProfessorSource", "ProfessorResearchArea",
    "ProfessorTopic",
]

src = sqlite3.connect(f"file:{SQLITE_PATH}?mode=ro", uri=True)
src.row_factory = sqlite3.Row

print(f"Source: {SQLITE_PATH}")
print(f"Target host: {host}")
print(f"Mode: {'DRY-RUN' if DRY_RUN else 'WRITE'}")

dst = psycopg.connect(DATABASE_URL)
dst.autocommit = False
report = []

try:
    with dst.cursor() as cur:
        for table in TABLES:
            try:
                cols = [r[1] for r in src.execute(f'PRAGMA table_info("{table}")')]
                rows = list(src.execute(f'SELECT * FROM "{table}"'))
            except sqlite3.Error as e:
                print(f"{table}\tSKIPPED (source missing: {e})")
                continue

            # Destination table existence
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = %s
                )
            """, (table,))
            if not cur.fetchone()[0]:
                print(f"{table}\tSKIPPED (destination table missing)")
                continue

            inserted = 0
            conflicts = 0
            for row in rows:
                values = [row[c] for c in cols]
                placeholders = ", ".join(["%s"] * len(cols))
                collist = ", ".join(f'"{c}"' for c in cols)
                if DRY_RUN:
                    # Count existing by primary key when possible
                    pk = cols[0] if cols else None
                    if pk:
                        cur.execute(f'SELECT 1 FROM "{table}" WHERE "{pk}" = %s LIMIT 1', (values[0],))
                        if cur.fetchone():
                            conflicts += 1
                        else:
                            inserted += 1
                    else:
                        inserted += 1
                    continue

                sql = f'''
                    INSERT INTO "{table}" ({collist}) VALUES ({placeholders})
                    ON CONFLICT DO NOTHING
                '''
                cur.execute(sql, values)
                if cur.rowcount == 1:
                    inserted += 1
                else:
                    conflicts += 1

            if not DRY_RUN:
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                after = cur.fetchone()[0]
            else:
                after = "(dry-run)"
            report.append((table, len(rows), after, inserted, conflicts))

    if DRY_RUN:
        dst.rollback()
        print("DRY-RUN complete — rolled back, no changes.")
    else:
        dst.commit()
        print("Committed.")
except Exception:
    dst.rollback()
    raise
finally:
    print("table\tsource\tdest_after\tinserted\tconflicts")
    for table, before, after, inserted, conflicts in report:
        print(f"{table}\t{before}\t{after}\t{inserted}\t{conflicts}")
    src.close()
    dst.close()
