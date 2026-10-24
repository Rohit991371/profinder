"""`python -m profinder_pipeline match ...`
  universities [--file F] [--dry-run]   registry result -> entity_type=university candidates (ingest.run kind=registry)
  run [--only NAME] [--mode additive|insert-only] [--dry-run]   match CURRENT candidates against the app DB, store action/diff/matched id
  show [--only NAME] [--entity E] [--action A] [--limit N]      print stored plans
The app database is opened READ-ONLY. Only ingest.candidate / ingest.run are written (unless --dry-run)."""
import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..paths import OUTPUT_DIR
from ..utils.placeholders import PlaceholderRegistry

OUT_DIR = OUTPUT_DIR / "match"
ENTITIES = ("university", "professor", "program", "scholarship")


def _connect(url: Optional[str], what: str, readonly: bool = False):
    from ..db.connection import connect
    from ..db.hostguard import refuse_production
    if not url:
        raise SystemExit(f"{what}: database URL is not set in pipeline/.env")
    print(f"{what} host:", refuse_production(url, what, "ALLOW_PRODUCTION_MATCH"))
    conn = connect(url)
    if readonly:
        conn.read_only = True
    return conn


def _rows(conn: Any, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    from psycopg.rows import dict_row
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


# ------------------------------------------------------------------------------------------ universities
def cmd_universities(args: argparse.Namespace) -> int:
    from ..db.ingest_store import finish_run, insert_candidate, start_run
    from ..schemas.enums import RunKind, RunStatus
    from ..settings import get_settings
    from .universities import build_candidate

    path = Path(args.file) if args.file else OUTPUT_DIR / "registry" / "universities_merged.json"
    merged = json.loads(path.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    cands, st = [], Counter()
    for m in merged:
        c = build_candidate(m, now)
        cands.append(c)
        st[c.decision.value] += 1
    print(f"{len(cands)} university candidates: {dict(st)}")
    if args.dry_run:
        print("dry-run: nothing written")
        return 0
    with _connect(get_settings().pipeline_database_url, "ingest") as conn:
        run_id = start_run(conn, RunKind.registry, {"stage": "university-candidates", "source_file": path.name})
        for c in cands:
            insert_candidate(conn, run_id, c)
        finish_run(conn, run_id, RunStatus.ok, {"candidates": len(cands), "decisions": dict(st)})
        conn.commit()
    print(f"written: {len(cands)} rows (run {run_id[:8]})")
    return 0


# ------------------------------------------------------------------------------------------ run
_SQL_CURRENT = ("SELECT candidate_id::text AS candidate_id, entity_type, university_name, payload, proposed_status, decision, checks "
                "FROM ingest.current_candidate WHERE entity_type = ANY(%s) AND decision NOT IN ('rejected', 'human_rejected')")
_SQL_UPDATE = ("UPDATE ingest.candidate SET matched_entity_id = %s, action = %s, diff = %s, payload = payload || %s, "
               "checks = (SELECT coalesce(jsonb_agg(e), '[]'::jsonb) FROM jsonb_array_elements(checks) e WHERE left(e->>'name', 2) <> 'M_') || %s "
               "WHERE candidate_id = %s")
_TABLE = {"professor": "Professor", "program": "Program", "scholarship": "Scholarship"}


def cmd_run(args: argparse.Namespace) -> int:
    from psycopg.types.json import Jsonb
    from ..settings import get_settings
    from .engine import plan_candidates

    s = get_settings()
    ing = _connect(s.pipeline_database_url, "ingest")
    app = _connect(s.app_database_url, "app (read-only)", readonly=True)
    sql, params = _SQL_CURRENT, [list(ENTITIES)]
    cands = _rows(ing, sql, (params[0],))
    if args.only:
        wanted = set(args.only)
        cands = [c for c in cands if c["university_name"] in wanted]
    if not cands:
        print("no current candidates found (run `extract run` / `match universities` first)")
        return 1
    universities = _rows(app, 'SELECT * FROM "University"')
    print(f"{len(cands)} current candidates; {len(universities)} existing universities")

    def load_rows(entity: str, uid: str) -> list[dict[str, Any]]:
        return _rows(app, f'SELECT * FROM "{_TABLE[entity]}" WHERE "universityId" = %s', (uid,))

    def load_areas(ids: list[str]) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        if ids:
            for r in _rows(app, 'SELECT pra."professorId" AS pid, ra.name AS name FROM "ProfessorResearchArea" pra '
                                'JOIN "ResearchArea" ra ON ra.id = pra."researchAreaId" WHERE pra."professorId" = ANY(%s)', (ids,)):
                out.setdefault(r["pid"], set()).add(r["name"])
        return out

    results = plan_candidates(cands, universities=universities, load_rows=load_rows, load_areas=load_areas,
                              registry=PlaceholderRegistry.from_yaml(), mode=args.mode, lock_policy=args.lock_policy)
    app.close()
    _report(results, args.mode)
    if args.dry_run:
        print("dry-run: ingest.candidate not updated")
        return 0
    with ing.transaction():
        for r in results:
            ing.execute(_SQL_UPDATE, (r.matched_id, r.action, Jsonb([c.as_json() for c in r.changes]), Jsonb(r.payload_patch),
                                      Jsonb([r.check] if r.check else []), r.candidate_id))
    ing.commit()
    print(f"updated {len(results)} candidates (matched_entity_id, action, diff, universityId, M_* check)")
    return 0


def _report(results: list, mode: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / "match_report.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["university", "entity", "label", "effective_decision", "action", "matched_id", "via", "locked", "enrich", "propose", "status", "note"])
        for r in results:
            n = Counter(c.kind for c in r.changes)
            w.writerow([r.university_name, r.entity_type, r.label, r.effective, r.action or "-", r.matched_id or "", r.via or "", int(r.locked),
                        n["enrich"], n["propose"], n["status"], r.note or ""])
    lines = [f"# Match report (mode {mode})", ""]
    by: Counter = Counter((r.university_name, r.entity_type, r.effective, r.action or "unplanned") for r in results)
    lines += ["| university | entity | effective decision | action | n |", "|---|---|---|---|---|"]
    lines += [f"| {u} | {e} | {d} | {a} | {n} |" for (u, e, d, a), n in sorted(by.items(), key=lambda kv: tuple(map(str, kv[0])))]
    unplanned = [r for r in results if r.action is None]
    locked = sum(1 for r in results if r.locked)
    lines += ["", f"Unplanned (ambiguous or university unresolved): {len(unplanned)}; human-touched existing rows (proposals only): {locked}"]
    (OUT_DIR / "match_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    tot = Counter(r.action or "unplanned" for r in results)
    print("actions:", dict(tot), "| effective:", dict(Counter(r.effective for r in results)), "| human-touched:", locked)
    print("reports:", OUT_DIR / "match_report.csv", "|", OUT_DIR / "match_report.md")


def cmd_show(args: argparse.Namespace) -> int:
    from ..settings import get_settings
    conn = _connect(get_settings().pipeline_database_url, "ingest")
    sql = ("SELECT university_name, entity_type, action, matched_entity_id, payload, diff, checks FROM ingest.current_candidate "
           "WHERE true")
    p: list[Any] = []
    if args.action == "unplanned":
        sql += " AND action IS NULL AND decision NOT IN ('rejected', 'human_rejected')"
        args.action = None
    for col, val in (("university_name", args.only), ("entity_type", args.entity), ("action", args.action)):
        if val:
            sql += f" AND {col} = %s"
            p.append(val)
    rows = _rows(conn, sql + " ORDER BY university_name, entity_type LIMIT %s", (*p, args.limit))
    for r in rows:
        pl = r["payload"]
        print(f"- {r['university_name']} | {r['entity_type']} | {pl.get('nameZh') or pl.get('name') or pl.get('major')} | action={r['action']} | match={r['matched_entity_id']}")
        for d in r["diff"] or []:
            print(f"    {d.get('kind', '?'):8} {d['field']}: {d.get('before')} -> {d.get('after')}")
        for c in r["checks"] or []:
            if c.get("name", "").startswith("M_") and not c.get("passed"):
                print(f"    CHECK FAILED {c['name']}: {c.get('detail')}")
    print(f"({len(rows)} shown)")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="profinder_pipeline match", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("universities", help="registry result -> university candidates")
    u.add_argument("--file")
    u.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("run", help="match current candidates and plan writes")
    r.add_argument("--only", action="append", default=[], help="university name (repeatable)")
    r.add_argument("--mode", choices=("additive", "insert-only"), default="additive")
    r.add_argument("--lock-policy", choices=("strict", "import-ok"), default="strict",
                   help="strict: updatedAt > lastSyncedAt+60s (or createdAt) = a person edited it. import-ok: script-made rows never synced by us are not locked")
    r.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("show", help="print stored plans")
    s.add_argument("--only")
    s.add_argument("--entity", choices=ENTITIES)
    s.add_argument("--action", choices=("insert", "enrich", "propose", "noop", "unplanned"))
    s.add_argument("--limit", type=int, default=10)
    args = ap.parse_args(argv)
    return {"universities": cmd_universities, "run": cmd_run, "show": cmd_show}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
