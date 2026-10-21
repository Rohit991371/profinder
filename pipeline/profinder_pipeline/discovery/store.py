from __future__ import annotations

import json
from dataclasses import asdict

from .crawl import Found, Result

_METHOD = {"seed": "seed", "link_follow": "link_follow", "search": "search"}

UPSERT = """
insert into ingest.target (university_name, university_id, kind, url, discovered_by, robots_status, status, parser_config, last_error)
values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
on conflict (url, kind) do update set
    university_id = excluded.university_id, discovered_by = excluded.discovered_by,
    robots_status = excluded.robots_status, parser_config = excluded.parser_config
where ingest.target.status in ('pending', 'no_page_found')
"""
DELETE_MISS = "delete from ingest.target where university_name = %s and kind = %s and status = 'no_page_found'"
OTHER_PENDING = "select discovered_by, parser_config->>'validated' from ingest.target where university_name = %s and kind = %s and status = 'pending' and url <> %s"
DELETE_OTHER_PENDING = "delete from ingest.target where university_name = %s and kind = %s and status = 'pending' and url <> %s"
EXISTS_REAL = "select 1 from ingest.target where university_name = %s and kind = %s and status <> 'no_page_found' limit 1"
OPEN_RUN = "insert into ingest.run (kind, params) values ('discover', %s::jsonb) returning run_id"
CLOSE_RUN = "update ingest.run set finished_at = now(), stats = %s::jsonb, status = %s where run_id = %s"


def _first(row):
    return list(row.values())[0] if isinstance(row, dict) else row[0]


def open_run(cur, params: dict) -> str:
    cur.execute(OPEN_RUN, (json.dumps(params),))
    return str(_first(cur.fetchone()))


def close_run(cur, run_id: str, stats: dict, status: str) -> None:
    cur.execute(CLOSE_RUN, (json.dumps(stats), status, run_id))


def _cfg(f: Found) -> str:
    return json.dumps({"validated": f.validated, "score": f.score, "via": f.via, "note": f.note}, ensure_ascii=False)


def save_result(cur, res: Result) -> dict:
    """Writes ingest.target only (pipeline state). Found -> pending/allowed. Miss -> one `no_page_found` row (url = where we looked),
    skipped if a real target for that university+kind already exists, so a flaky re-run never hides a good target.
    A stale `no_page_found` row is removed when a real target for the same university+kind is stored."""
    n = {"found": 0, "kept_existing": 0, "miss_recorded": 0, "miss_skipped": 0}
    for f in res.found:
        cur.execute(OTHER_PENDING, (res.name, f.kind, f.url))
        others = [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in cur.fetchall()]
        if (f.method != "seed" and any(o[0] == "seed" for o in others)) or (f.validated is False and any(o[1] == "true" for o in others)):
            n["kept_existing"] += 1          # a seed, or a validated target, is never replaced by a weaker one
            continue
        cur.execute(DELETE_MISS, (res.name, f.kind))
        cur.execute(DELETE_OTHER_PENDING, (res.name, f.kind, f.url))
        cur.execute(UPSERT, (res.name, res.university_id, f.kind, f.url, _METHOD[f.method], "allowed", "pending", _cfg(f), None))
        n["found"] += 1
    for kind in res.missing:
        cur.execute(EXISTS_REAL, (res.name, kind))
        if cur.fetchone():
            n["miss_skipped"] += 1
            continue
        cfg = json.dumps({"searched": kind in res.searched, "pages_fetched": res.pages_fetched, "notes": res.notes, "blocked": res.blocked,
                          "offdomain_candidates": res.offdomain}, ensure_ascii=False)
        cur.execute(UPSERT, (res.name, res.university_id, kind, res.start_url or f"urn:no-start-url:{res.name}", "search" if kind in res.searched else "link_follow",
                             "unknown", "no_page_found", cfg, "; ".join(res.notes)[:300] or None))
        n["miss_recorded"] += 1
    return n


def result_to_dict(res: Result) -> dict:
    return asdict(res)


LIST_PENDING_DUPES = (
    "select university_name, kind, target_id, url, discovered_by, parser_config->>'validated' from ingest.target "
    "where status = 'pending' and (university_name, kind) in "
    "(select university_name, kind from ingest.target where status = 'pending' group by 1, 2 having count(*) > 1) order by 1, 2, 4"
)
DELETE_BY_ID = "delete from ingest.target where target_id = %s and status = 'pending'"
DELETE_REJECTED = ("delete from ingest.target where university_name = %s and kind = %s and url = %s "
                   "and status = 'pending' and discovered_by <> 'seed'")


def _rank(row: tuple) -> tuple:
    """Smaller = better: seed, then validated, then link_follow before search, then URL (stable)."""
    _name, _kind, _id, url, by, validated = row
    return (0 if by == "seed" else 1, 0 if validated == "true" else 1, 0 if by == "link_follow" else 1, url)


def plan_dedupe(rows: list[tuple]) -> tuple[list[tuple], list[tuple]]:
    """rows of LIST_PENDING_DUPES -> (keep, delete): one target per university+kind, best by _rank."""
    groups: dict[tuple, list[tuple]] = {}
    for r in rows:
        groups.setdefault((r[0], r[1]), []).append(r)
    keep, drop = [], []
    for g in groups.values():
        g.sort(key=_rank)
        keep.append(g[0])
        drop += g[1:]
    return keep, drop
