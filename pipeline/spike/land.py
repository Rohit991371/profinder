"""Task 0.6 — land candidates.jsonl into Postgres schema `spike` and query it back.

Safety: only touches schema `spike`; refuses any host except localhost/127.0.0.1; logs host:port only.
Usage (from pipeline/):  python -m spike.land            # create/refresh spike.professor_candidate
                         python -m spike.land --drop     # drop schema spike (Iteration 1 does this)
"""
from __future__ import annotations

import argparse
import json
from urllib.parse import urlsplit

import psycopg
from psycopg.types.json import Jsonb

from .common import OUT_DIR, ensure_dirs, env


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--drop", action="store_true", help="drop schema spike and exit")
    args = ap.parse_args()
    ensure_dirs()
    url = env("PIPELINE_DATABASE_URL")
    if not url:
        raise SystemExit("PIPELINE_DATABASE_URL is not set in pipeline/.env")
    parts = urlsplit(url)
    if parts.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise SystemExit(f"Refusing to run: host '{parts.hostname}' is not local")
    print(f"database host: {parts.hostname}:{parts.port}")

    with psycopg.connect(url) as conn:
        if args.drop:
            conn.execute("DROP SCHEMA IF EXISTS spike CASCADE")
            print("schema spike dropped")
            return
        lines = [ln for ln in (OUT_DIR / "candidates.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
        conn.execute("CREATE SCHEMA IF NOT EXISTS spike")
        conn.execute("""CREATE TABLE IF NOT EXISTS spike.professor_candidate (
            id serial PRIMARY KEY, name_zh text NOT NULL, decision text NOT NULL,
            payload jsonb NOT NULL, loaded_at timestamptz NOT NULL DEFAULT now())""")
        conn.execute("TRUNCATE spike.professor_candidate")
        with conn.cursor() as cur:
            for ln in lines:
                rec = json.loads(ln)
                cur.execute("INSERT INTO spike.professor_candidate (name_zh, decision, payload) VALUES (%s, %s, %s)",
                            (rec["name_zh"], rec["decision"], Jsonb(rec)))
        conn.commit()
        db_rows = conn.execute("SELECT count(*) FROM spike.professor_candidate").fetchone()[0]
        by_decision = dict(conn.execute("SELECT decision, count(*) FROM spike.professor_candidate GROUP BY 1").fetchall())
        sample = conn.execute("SELECT name_zh, payload->>'email' FROM spike.professor_candidate ORDER BY id LIMIT 3").fetchall()

    result = {"jsonl_lines": len(lines), "db_rows": db_rows, "by_decision": by_decision}
    (OUT_DIR / "land_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"JSONL lines: {len(lines)} | DB rows: {db_rows} | match: {len(lines) == db_rows}")
    print("by decision:", by_decision)
    print("sample:", sample)


if __name__ == "__main__":
    main()
