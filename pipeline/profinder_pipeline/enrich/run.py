"""`python -m profinder_pipeline enrich ...` (task 6.1): OpenAlex ids for EXISTING official-sourced professors only (D9).

  enrich probe  --only NAME [--name "Zhu Jun"]     one live search; shows the raw keys, the parsed author and the decision (1 API call)
  enrich run    [--only NAME ...] [--limit N] [--max-calls N] [--refresh]   read the app DB (read-only), search, write output/enrich/<id>/
  enrich show   --enrich ID [--limit N]
  enrich approve --enrich ID --by NAME             a person has hand-checked qa_sample.csv on openalex.org
  enrich load   --enrich ID [--apply] [--lock-policy strict|import-ok]   dry-run default; apply needs CONFIRM_LOAD=<id> and an approved bundle
  enrich rollback --enrich ID [--apply]            needs CONFIRM_ROLLBACK=<id>
A bundle is never written to ingest.candidate (the newest-run view would hide the extraction candidates)."""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..paths import INPUTS_DIR, OUTPUT_DIR
from ..utils.placeholders import PlaceholderRegistry
from .authors import DEFAULT_FIELDS, decide, query_name, search_authors, short_id
from .engine import OFFICIAL_SOURCES, build_line, detect_url_style, eligible, jsonl, run_enrich_load, sha256_text

OUT = OUTPUT_DIR / "enrich"
CACHE = INPUTS_DIR / "cache"
FILES = ("enrichments.jsonl", "review.csv", "qa_sample.csv")
HUMAN_EDITED = ("qa_sample.csv",)    # the reviewer fills qa_result in this file, so it cannot be part of the checksum check


def _read_conn(prog: str):
    from ..db.connection import connect
    from ..db.hostguard import url_host
    from ..settings import get_settings
    url = get_settings().app_database_url
    if not url:
        raise SystemExit("APP_DATABASE_URL is not set in pipeline/.env")
    host = url_host(url)
    if any(w in host.lower() for w in ("prod", "production")) and os.environ.get("ALLOW_PRODUCTION_ENRICH_READ") != "1":
        raise SystemExit(f"refusing host {host}: looks like production (the owner may set ALLOW_PRODUCTION_ENRICH_READ=1 for this read-only command)")
    print(f"app host (read-only): {host}")
    conn = connect(url)
    conn.read_only = True
    return conn


def _rows(conn, sql: str, params: tuple = ()) -> list:
    from ..load.db import PgDb
    return PgDb(conn).rows(sql, params)


def _eligible_rows(conn, only: list, limit: Optional[int]) -> list:
    marks = ", ".join(["%s"] * len(OFFICIAL_SOURCES))
    sql = (f'SELECT p.*, u."openAlexId" AS "_uniOa", u.name AS "_uniName" FROM "Professor" p JOIN "University" u ON u.id = p."universityId" '
           f'WHERE p."dataSource" IN ({marks}) AND (p."openAlexId" IS NULL OR p."openAlexId" = \'\')')
    params: list = list(OFFICIAL_SOURCES)
    if only:
        sql += " AND u.name IN (" + ", ".join(["%s"] * len(only)) + ")"
        params += only
    sql += " ORDER BY u.name, p.name"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return _rows(conn, sql, tuple(params))


def _styles(conn) -> dict:
    oa = [r["openAlexId"] for r in _rows(conn, 'SELECT "openAlexId" FROM "Professor" WHERE "openAlexId" IS NOT NULL LIMIT 500')]
    oc = [r["orcid"] for r in _rows(conn, 'SELECT orcid FROM "Professor" WHERE orcid IS NOT NULL LIMIT 500')]
    return {"openAlexId": detect_url_style(oa), "orcid": detect_url_style(oc)}


def _client():
    import httpx
    return httpx.Client(headers={"User-Agent": "ProFinderPipeline/0.6"}, follow_redirects=True)


def cmd_probe(args) -> int:
    from ..settings import get_settings
    conn = _read_conn("probe")
    rows = [r for r in _eligible_rows(conn, args.only, None) if eligible(r) is None]
    if args.name:
        rows = [r for r in rows if r["name"].casefold() == args.name.casefold()]
    if not rows:
        print("no eligible professor found")
        return 1
    prof = rows[0]
    inst = short_id(prof.get("_uniOa"))
    q = query_name(prof["name"])
    print(f"professor: {prof['name']} / {prof.get('nameZh')} | university: {prof['_uniName']} | institution id: {inst} | query: {q!r}")
    from .authors import cache_file
    key = get_settings().openalex_api_key
    authors, net = search_authors(_client(), q, inst, CACHE, key, refresh=args.refresh)
    p = cache_file(CACHE, inst, q)
    raw = json.loads(p.read_text(encoding="utf-8"))
    print("network call:", net, "| top-level keys:", sorted(raw)[:12])
    first = (raw.get("results") or [{}])[0]
    print("first result keys:", sorted(first)[:40])
    print("parsed authors:", len(authors))
    for a in authors[:5]:
        print(f"  {a.openalex_id} {a.name!r} works={a.works_count} orcid={a.orcid} institutions={sorted(a.institution_ids)[:4]} fields={sorted(a.fields)[:4]}")
    o = decide(prof, authors, inst, tuple(args.field or DEFAULT_FIELDS))
    print("decision:", o.status, o.reason, o.checks)
    print(f"raw response cached at {p.relative_to(INPUTS_DIR.parent)} (key not stored). Paste the two 'keys' lines back.")
    return 0


def cmd_run(args) -> int:
    from ..settings import get_settings
    conn = _read_conn("run")
    styles = _styles(conn)
    rows = _eligible_rows(conn, args.only, args.limit)
    key = get_settings().openalex_api_key
    now = datetime.now(timezone.utc)
    eid = f"enr_{now:%Y-%m-%d}_{uuid.uuid4().hex[:6]}"
    client = _client()
    strong, review, counts, calls, stopped = [], [], Counter(), 0, None
    allowed = tuple(args.field or DEFAULT_FIELDS)
    for i, prof in enumerate(rows, 1):
        why = eligible(prof)
        if why:
            counts[f"skip:{why}"] += 1
            continue
        inst = short_id(prof.get("_uniOa"))
        if not inst:
            counts["skip:university_has_no_openalex_id"] += 1
            continue
        q = query_name(prof["name"])
        if not q:
            counts["skip:no_latin_name"] += 1
            continue
        try:
            authors, net = search_authors(client, q, inst, CACHE, key, refresh=args.refresh)
        except RuntimeError as exc:
            counts["error:http"] += 1
            print(f"  {prof['name']}: {exc}")
            if counts["error:http"] >= 5:
                stopped = "5 HTTP errors (rate limit or key?): stopped; cached answers are kept, re-run to continue"
                break
            continue
        calls += 1 if net else 0
        o = decide(prof, authors, inst, allowed)
        counts[f"{o.status}:{o.reason}"] += 1
        if o.status == "strong":
            strong.append(build_line(prof, o.author, o.reason, o.checks, styles, now))
        elif o.status == "review":
            review.append({"professor_id": prof["id"], "university": prof["_uniName"], "name": prof["name"], "nameZh": prof.get("nameZh") or "",
                           "reason": o.reason, "author_url": o.author.url if o.author else "", "author_name": o.author.name if o.author else "",
                           "works_count": o.author.works_count if o.author else "", "others": " ".join(a.url for a in o.others[:4])})
        if calls >= args.max_calls:
            stopped = f"--max-calls {args.max_calls} reached after {i} of {len(rows)} professors; re-run to continue (cached searches are free)"
            break
        if i % 50 == 0:
            print(f"  {i}/{len(rows)} professors, {calls} API calls, strong {len(strong)}")
    folder = OUT / eid
    folder.mkdir(parents=True, exist_ok=True)
    texts = {"enrichments.jsonl": jsonl(sorted(strong, key=lambda x: (x["university_id"] or "", x["candidate_id"])))}
    texts["review.csv"] = _csv(review, ["professor_id", "university", "name", "nameZh", "reason", "author_url", "author_name", "works_count", "others"])
    sample = random.Random(f"{eid}-qa").sample(strong, min(len(strong), max(10, min(30, -(-len(strong) // 20)))))
    texts["qa_sample.csv"] = _csv([{"candidate_id": s["candidate_id"], "university_id": s["university_id"], "name": s["name"], "nameZh": s["nameZh"] or "",
                                    "author_url": s["author_url"], "author_name": s["author_name"], "fields": json.dumps(s["fields"], ensure_ascii=False),
                                    "qa_result": "", "note": ""} for s in sample],
                                  ["candidate_id", "university_id", "name", "nameZh", "author_url", "author_name", "fields", "qa_result", "note"])
    for n, t in texts.items():
        (folder / n).write_text(t, encoding="utf-8-sig" if n.endswith(".csv") else "utf-8")
    man = {"enrich_id": eid, "created_at": now.isoformat(timespec="seconds"), "styles": styles, "allowed_fields": list(allowed),
           "counts": dict(sorted(counts.items())), "lines": len(strong), "review_rows": len(review), "api_calls": calls, "stopped": stopped,
           "files": {n: {"sha256": sha256_text(t), "lines": t.count("\n")} for n, t in sorted(texts.items())}}
    (folder / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    (folder / "report.md").write_text(_report(man), encoding="utf-8")
    print(f"bundle {eid}: {len(strong)} strong matches, {len(review)} for review, {calls} API calls (network); folder {folder}")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")
    if stopped:
        print("NOTE:", stopped)
    print(f"next: check qa_sample.csv on openalex.org, fill qa_result (ok/error), then: enrich approve --enrich {eid} --by \"<name>\"")
    return 0


def _csv(rows: list, cols: list) -> str:
    import io
    b = io.StringIO()
    w = csv.DictWriter(b, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return b.getvalue()


def _report(m: dict) -> str:
    lines = [f"# Enrichment bundle {m['enrich_id']}", "", f"Created {m['created_at']} · API calls (network) {m['api_calls']} · allowed fields {', '.join(m['allowed_fields'])}",
             f"Value style of existing rows: {m['styles']}", "", "## Outcomes", ""]
    lines += [f"- {k}: {v}" for k, v in m["counts"].items()]
    lines += ["", f"Strong matches (in enrichments.jsonl): {m['lines']} · for review (review.csv): {m['review_rows']}",
              "", "Written only when the field is empty: openAlexId, orcid, publicationCount. Status, name, email and every other field are untouched.", ""]
    if m.get("stopped"):
        lines += ["**Stopped early:** " + m["stopped"], ""]
    return "\n".join(lines) + "\n"


def _folder(eid: str) -> Path:
    f = OUT / eid
    if not (f / "manifest.json").exists():
        found = [d for d in OUT.glob("enr_*") if d.name.startswith(eid) or eid in d.name] if OUT.exists() else []
        if len(found) != 1:
            raise SystemExit(f"no unique enrichment bundle for {eid!r} in {OUT}")
        f = found[0]
    return f


def verify(folder: Path) -> list:
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    bad = []
    for n, info in man["files"].items():
        if n in HUMAN_EDITED:
            continue
        t = (folder / n).read_text(encoding="utf-8-sig" if n.endswith(".csv") else "utf-8")
        if sha256_text(t) != info["sha256"]:
            bad.append(f"{n}: checksum differs")
    return bad


def qa_status(folder: Path) -> dict:
    p = folder / "qa_sample.csv"
    rows = list(csv.DictReader(p.open(encoding="utf-8-sig"))) if p.exists() else []
    done = [r for r in rows if (r.get("qa_result") or "").strip().lower() in ("ok", "error")]
    err = [r for r in done if r["qa_result"].strip().lower() == "error"]
    return {"sampled": len(rows), "checked": len(done), "errors": len(err), "pass": bool(rows) and len(done) == len(rows) and len(err) == 0}


def cmd_show(args) -> int:
    f = _folder(args.enrich)
    print((f / "report.md").read_text(encoding="utf-8"))
    for i, ln in enumerate((f / "enrichments.jsonl").read_text(encoding="utf-8").splitlines()[: args.limit]):
        d = json.loads(ln)
        print(f"{d['name']} / {d['nameZh']} -> {d['author_url']} {d['fields']} ({d['reason']})")
    print("verify:", verify(f) or "OK", "| qa:", qa_status(f))
    return 0


def cmd_approve(args) -> int:
    f = _folder(args.enrich)
    bad = verify(f)
    if bad:
        print("bundle does not verify:", bad)
        return 1
    qa = qa_status(f)
    if not qa["pass"]:
        print("QA gate not passed (every row of qa_sample.csv needs qa_result ok, zero errors):", qa)
        return 1
    man = json.loads((f / "manifest.json").read_text(encoding="utf-8"))
    (f / "approved.json").write_text(json.dumps({"enrich_id": man["enrich_id"], "approved_by": args.by, "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                 "lines_sha256": man["files"]["enrichments.jsonl"]["sha256"], "qa": qa}, indent=2), encoding="utf-8")
    print("approved by", args.by)
    return 0


def _approved(f: Path) -> bool:
    p = f / "approved.json"
    if not p.exists():
        return False
    man = json.loads((f / "manifest.json").read_text(encoding="utf-8"))
    return json.loads(p.read_text(encoding="utf-8")).get("lines_sha256") == man["files"]["enrichments.jsonl"]["sha256"]


def cmd_load(args) -> int:
    from ..load.db import PgDb
    from ..load.run import _app_connection, _write_report
    f = _folder(args.enrich)
    man = json.loads((f / "manifest.json").read_text(encoding="utf-8"))
    eid = man["enrich_id"]
    bad = verify(f)
    if bad:
        print("bundle does not verify:", bad)
        return 1
    if args.apply and not _approved(f):
        print("only an approved bundle can be applied: run `enrich approve` after the QA sample is checked")
        return 1
    lines = [json.loads(x) for x in (f / "enrichments.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    conn, host = _app_connection(args.apply, eid, "CONFIRM_LOAD")
    db = PgDb(conn)
    try:
        result = run_enrich_load(db, lines, eid, PlaceholderRegistry.from_yaml(), args.apply, datetime.now(timezone.utc), args.lock_policy)
        db.commit() if args.apply else db.rollback()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"LOAD ABORTED, nothing was written: {type(exc).__name__}: {exc}")
        return 1
    plan = result["plan"]
    print("plan:", json.dumps(plan["summary"], ensure_ascii=False))
    for s in plan["skipped"][:15]:
        print("  skipped:", s)
    if plan["dropped_columns"]:
        print("  columns not in the target table (dropped):", plan["dropped_columns"])
    _write_report("enrich_applied" if args.apply else "enrich_dryrun", eid.replace("enr_", ""), result)
    print("LOADED. done: " + json.dumps(result["done"]) if args.apply else "dry-run only: nothing was written. To apply: CONFIRM_LOAD=<id> and --apply")
    return 0


def cmd_rollback(args) -> int:
    from ..load.db import PgDb
    from ..load.rollback import run_rollback
    from ..load.run import _app_connection, _write_report
    eid = json.loads((_folder(args.enrich) / "manifest.json").read_text(encoding="utf-8"))["enrich_id"]
    conn, host = _app_connection(args.apply, eid, "CONFIRM_ROLLBACK")
    db = PgDb(conn)
    try:
        result = run_rollback(db, eid, args.apply)
        db.commit() if args.apply else db.rollback()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"ROLLBACK ABORTED, nothing was changed: {type(exc).__name__}: {exc}")
        return 1
    print("plan:", json.dumps(result["plan"]["summary"], ensure_ascii=False))
    for s in result["plan"]["skipped"][:20]:
        print("  kept:", s)
    _write_report("enrich_rollback_applied" if args.apply else "enrich_rollback_dryrun", eid.replace("enr_", ""), result)
    print("ROLLED BACK." if args.apply else "dry-run only. To apply: CONFIRM_ROLLBACK=<id> and --apply")
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="profinder_pipeline enrich", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("probe", "run"):
        p = sub.add_parser(n)
        p.add_argument("--only", action="append", default=[], help="university name (repeatable)")
        p.add_argument("--field", action="append", help="allowed OpenAlex field (repeatable); default: " + ", ".join(DEFAULT_FIELDS))
        p.add_argument("--refresh", action="store_true", help="ignore the cached search answer")
        if n == "probe":
            p.add_argument("--name", help="romanised name as stored, e.g. 'Zhu Jun'")
        else:
            p.add_argument("--limit", type=int, help="first N eligible professors")
            p.add_argument("--max-calls", type=int, default=300, help="stop after N network calls (default 300; cached searches are free)")
    p = sub.add_parser("show")
    p.add_argument("--enrich", required=True)
    p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("approve")
    p.add_argument("--enrich", required=True)
    p.add_argument("--by", required=True)
    for n in ("load", "rollback"):
        p = sub.add_parser(n)
        p.add_argument("--enrich", required=True)
        p.add_argument("--apply", action="store_true")
        if n == "load":
            p.add_argument("--lock-policy", choices=("strict", "import-ok"), default="strict")
    a = ap.parse_args(argv)
    return {"probe": cmd_probe, "run": cmd_run, "show": cmd_show, "approve": cmd_approve, "load": cmd_load, "rollback": cmd_rollback}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
