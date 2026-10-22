"""Database access for the fetch stage. Writes only to schema `ingest` (run, target, page). Never touches app tables.
`conn` is a psycopg connection from profinder_pipeline.db.connection.connect(); rows are read as tuples or dicts."""
from __future__ import annotations

import json
from typing import Optional

from .cache import url_hash
from .engine import FetchedPage


def _val(row, idx: int, key: str):
    return row[key] if isinstance(row, dict) else row[idx]


def load_targets(conn, names: Optional[list], kind: str = "faculty_list", statuses=("pending", "ok", "failed")) -> list:
    sql = ("select target_id, university_name, url, status, parser_config from ingest.target "
           "where kind = %s and status = any(%s)")
    params: list = [kind, list(statuses)]
    if names:
        sql += " and university_name = any(%s)"
        params.append(list(names))
    cur = conn.execute(sql + " order by university_name, url", params)
    out = []
    for r in cur.fetchall():
        out.append({"target_id": str(_val(r, 0, "target_id")), "university_name": _val(r, 1, "university_name"),
                    "url": _val(r, 2, "url"), "status": _val(r, 3, "status"), "parser_config": _val(r, 4, "parser_config") or {}})
    return out


def start_run(conn, params: dict) -> str:
    cur = conn.execute("insert into ingest.run (kind, params) values ('crawl', %s::jsonb) returning run_id", [json.dumps(params)])
    run_id = str(_val(cur.fetchone(), 0, "run_id"))
    conn.commit()
    return run_id


def finish_run(conn, run_id: str, stats: dict, status: str) -> None:
    conn.execute("update ingest.run set finished_at = now(), stats = %s::jsonb, status = %s where run_id = %s",
                 [json.dumps(stats), status, run_id])
    conn.commit()


def upsert_profile_targets(conn, university_name: str, urls: list, list_url: str) -> dict:
    """One faculty_profile target per URL (status pending). Existing rows are left alone. -> {url: target_id}"""
    cfg = json.dumps({"from_list": list_url})
    for u in urls:
        conn.execute(
            "insert into ingest.target (university_name, kind, url, discovered_by, parser_config) "
            "values (%s, 'faculty_profile', %s, 'link_follow', %s::jsonb) on conflict (url, kind) do nothing",
            [university_name, u, cfg])
    ids: dict = {}
    if urls:
        cur = conn.execute("select url, target_id from ingest.target where kind = 'faculty_profile' and url = any(%s)", [list(urls)])
        for r in cur.fetchall():
            ids[_val(r, 0, "url")] = str(_val(r, 1, "target_id"))
    conn.commit()
    return ids


def update_target(conn, target_id: str, *, status: str, robots_status: str, error: Optional[str], parser_note: dict) -> None:
    """status ok -> fail_count 0, error cleared, next_fetch_at +30 days. Any other status -> fail_count + 1 (except blocked)."""
    ok = status == "ok"
    conn.execute(
        "update ingest.target set status = %s, robots_status = %s, last_error = %s, last_fetch_at = now(), "
        "fail_count = case when %s then 0 when %s = 'blocked' then fail_count else fail_count + 1 end, "
        "next_fetch_at = case when %s then now() + interval '30 days' else next_fetch_at end, "
        "parser_config = parser_config || %s::jsonb where target_id = %s",
        [status, robots_status, None if ok else error, ok, status, ok, json.dumps(parser_note), target_id])
    conn.commit()


def _latest_hash(conn, uhash: str) -> Optional[str]:
    cur = conn.execute("select content_hash from ingest.page where url_hash = %s order by fetched_at desc limit 1", [uhash])
    row = cur.fetchone()
    return _val(row, 0, "content_hash") if row else None


def persist_page(conn, fp: FetchedPage, target_id: Optional[str]) -> str:
    """-> 'inserted' | 'unchanged' | 'skipped'. Cache hits insert a row only if the database has none for this content
    (so a second run writes nothing); real fetches always insert one row (history); failed network attempts insert an error row."""
    uh = url_hash(fp.url)
    if fp.ok:
        if fp.from_cache and _latest_hash(conn, uh) == fp.content_hash:
            return "unchanged"
        conn.execute(
            "insert into ingest.page (target_id, url, final_url, url_hash, fetched_at, http_status, render_mode, encoding, "
            "content_hash, markdown, error) values (%s, %s, %s, %s, coalesce(%s, now()), %s, %s, %s, %s, %s, null)",
            [target_id, fp.url, fp.final_url, uh, fp.fetched_at, fp.status, fp.render_mode, fp.encoding, fp.content_hash, fp.markdown])
        conn.commit()
        return "inserted"
    if fp.network:
        conn.execute(
            "insert into ingest.page (target_id, url, final_url, url_hash, http_status, render_mode, error) "
            "values (%s, %s, %s, %s, %s, %s, %s)",
            [target_id, fp.url, fp.final_url or None, uh, fp.status, fp.render_mode, fp.error])
        conn.commit()
        return "inserted"
    return "skipped"
