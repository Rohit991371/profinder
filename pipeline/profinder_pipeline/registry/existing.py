from __future__ import annotations

import csv
import os
from pathlib import Path
from urllib.parse import urlsplit

from .merge import Existing

SQL = 'select id, name, "agencyNumber", "officialUrl", province, city, "openAlexId" from "University" order by name'


def load_existing_db(url: str | None = None) -> list[Existing]:
    """READ-ONLY query of the University table (session is set read-only). Logs host name only."""
    import psycopg

    url = url or os.environ.get("APP_DATABASE_URL", "")
    if not url:
        raise RuntimeError("APP_DATABASE_URL not set")
    host = urlsplit(url).hostname
    with psycopg.connect(url, autocommit=True, connect_timeout=10) as conn:
        conn.execute("set default_transaction_read_only = on")
        rows = conn.execute(SQL).fetchall()
    print(f"existing University rows: {len(rows)} (host {host})")
    return [Existing(*(None if v is None else str(v) for v in r)) for r in rows]


def load_existing_csv(path: str | Path) -> list[Existing]:
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return [Existing(r["id"], r["name"], r.get("agencyNumber"), r.get("officialUrl"), r.get("province"), r.get("city"), r.get("openAlexId")) for r in csv.DictReader(f)]
