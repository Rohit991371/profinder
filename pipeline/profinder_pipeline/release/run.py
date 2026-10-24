"""`python -m profinder_pipeline release ...`
  build [--only NAME] [--dry-run]      freeze the released candidates into output/releases/<date>_<id>/ and register a draft in ingest.release
  verify --release ID                  recompute the checksums of the folder
  qa-status --release ID               QA gate (5 % sample, professors <= 2 % errors, others 0)
  approve --release ID --by NAME       needs verify OK and the QA gate to pass
  list
Nothing here touches the app tables."""
import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# from ..paths import OUTPUT_DIR, PIPELINE_ROOT
from ..paths import OUTPUT_DIR, PIPELINE_DIR as PIPELINE_ROOT
from ..review.csvio import write_csv
from .builder import FILES, SOURCE_FILE, build_files, choose_qa, is_selected, manifest, qa_gate, report_text, sha256_text

RELEASE_DIR = OUTPUT_DIR / "releases"
CANDIDATE_SQL = ("SELECT c.candidate_id::text AS candidate_id, c.entity_type, c.university_name, c.natural_key, c.payload, c.source_url, c.page_id::text AS page_id, "
                 "c.evidence, c.checks, c.proposed_status, c.decision, c.reject_reason, c.matched_entity_id, c.action, c.diff, c.confidence, c.extractor, "
                 "c.qa_sample, c.qa_result, c.release_id::text AS release_id, c.reviewer, c.review_note, p.fetched_at, p.http_status, p.content_hash, "
                 "rel.status AS release_status FROM ingest.current_candidate c LEFT JOIN ingest.page p ON p.page_id = c.page_id "
                 "LEFT JOIN ingest.release rel ON rel.release_id = c.release_id")


def connect_ingest():
    from ..db.connection import connect
    from ..db.hostguard import refuse_production
    from ..settings import get_settings
    url = get_settings().pipeline_database_url
    if not url:
        raise SystemExit("PIPELINE_DATABASE_URL is not set in pipeline/.env")
    print("ingest host:", refuse_production(url, "ingest", "ALLOW_PRODUCTION_RELEASE"))
    return connect(url)


def fetch_candidates(conn: Any, only: Optional[list[str]] = None, release_id: Optional[str] = None) -> list[dict[str, Any]]:
    from psycopg.rows import dict_row
    sql, params = CANDIDATE_SQL + " WHERE true", []
    if only:
        sql += " AND c.university_name = ANY(%s)"
        params.append(list(only))
    if release_id:
        sql += " AND c.release_id = %s::uuid"
        params.append(release_id)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def config_hash() -> str:
    parts = []
    for p in sorted((PIPELINE_ROOT / "config").rglob("*.yml")):
        if "_proposed" not in p.parts:
            parts.append(p.relative_to(PIPELINE_ROOT).as_posix() + hashlib.sha256(p.read_bytes()).hexdigest())
    return sha256_text("".join(parts))[:16]


def git_sha() -> Optional[str]:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10, cwd=PIPELINE_ROOT).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def cmd_build(args: argparse.Namespace) -> int:
    from psycopg.types.json import Jsonb
    from ..db.ingest_store import finish_run, start_run
    from ..schemas.enums import RunKind, RunStatus
    conn = connect_ingest()
    cands = fetch_candidates(conn, args.only)
    if not cands:
        print("no current candidates")
        return 1
    if any(c["action"] is None and c["decision"] not in ("rejected", "human_rejected") for c in cands):
        print("note: some candidates have no action (run `match run` first, or they are ambiguous): they stay in the review queue")
    qa_ids = choose_qa(cands)
    for c in cands:
        if c["candidate_id"] in qa_ids:
            c["qa_sample"] = True
    texts, stats = build_files(cands)
    sel = [c for c in cands if is_selected(c)]
    print(f"{len(sel)} candidates released; counts {json.dumps(stats['counts'], ensure_ascii=False)}; QA sample {len(qa_ids)}; D7 dropped {stats['d7_dropped']}")
    if args.dry_run:
        print("dry-run: nothing written")
        return 0
    now = datetime.now(timezone.utc)
    rid = str(uuid.uuid4())
    folder = RELEASE_DIR / f"{now:%Y-%m-%d}_{rid[:6]}"
    folder.mkdir(parents=True, exist_ok=True)
    unis = sorted({c["university_name"] for c in sel if c.get("university_name")})
    man = manifest(rid, now, git_sha(), config_hash(), texts, stats, unis, args.mode)
    for name, text in texts.items():
        (folder / name).write_text(text, encoding="utf-8", newline="\n")
    for c in cands:                                    # the CSV needs the release folder name only for the report
        c["release_id"] = rid if is_selected(c) else c["release_id"]
    n_review = write_csv(folder / "needs_review.csv", cands)
    qa = qa_gate([c for c in cands if c["candidate_id"] in qa_ids or c.get("qa_sample")])
    (folder / "report.md").write_text(report_text(rid, cands, stats, man, qa), encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    run_id = start_run(conn, RunKind.release, {"release_id": rid, "only": args.only, "mode": args.mode}, git_sha())
    conn.execute("UPDATE ingest.release SET status = 'abandoned' WHERE status = 'draft'")
    conn.execute("INSERT INTO ingest.release (release_id, created_at, manifest, status) VALUES (%s, %s, %s, 'draft')", (rid, now, Jsonb(man)))
    if sel:
        conn.execute("UPDATE ingest.candidate SET release_id = %s WHERE candidate_id = ANY(%s::uuid[])", (rid, [c["candidate_id"] for c in sel]))
    if qa_ids:
        conn.execute("UPDATE ingest.candidate SET qa_sample = true WHERE candidate_id = ANY(%s::uuid[])", (sorted(qa_ids),))
    finish_run(conn, run_id, RunStatus.ok, {"released": len(sel), "needs_review_rows": n_review, "qa_sample": len(qa_ids)})
    conn.commit()
    print(f"release {rid}\nfolder  {folder}\nchecksum {man['release_checksum']}\nneeds_review.csv rows: {n_review}")
    return 0


def _folder(rid: str) -> Path:
    hits = sorted(RELEASE_DIR.glob(f"*_{rid[:6]}")) if RELEASE_DIR.exists() else []
    if not hits:
        raise SystemExit(f"no release folder for {rid} under {RELEASE_DIR}")
    return hits[-1]


def verify_folder(folder: Path) -> list[str]:
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    for name, f in man["files"].items():
        p = folder / name
        if not p.exists():
            problems.append(f"missing {name}")
        elif sha256_text(p.read_text(encoding="utf-8")) != f["sha256"]:
            problems.append(f"checksum differs: {name}")
    return problems


def cmd_verify(args: argparse.Namespace) -> int:
    folder = _folder(args.release)
    problems = verify_folder(folder)
    print(("VERIFY OK " if not problems else "VERIFY FAILED ") + str(folder) + ("" if not problems else "\n  " + "\n  ".join(problems)))
    return 0 if not problems else 1


def cmd_qa(args: argparse.Namespace) -> int:
    conn = connect_ingest()
    gate = qa_gate(fetch_candidates(conn, release_id=args.release))
    print(json.dumps(gate, ensure_ascii=False))
    return 0 if gate["pass"] else 1


def cmd_approve(args: argparse.Namespace) -> int:
    folder = _folder(args.release)
    problems = verify_folder(folder)
    conn = connect_ingest()
    gate = qa_gate(fetch_candidates(conn, release_id=args.release))
    if problems or not gate["pass"]:
        print("NOT approved:", problems or "", json.dumps(gate, ensure_ascii=False))
        return 1
    cur = conn.execute("UPDATE ingest.release SET status = 'approved', approved_by = %s, approved_at = now() WHERE release_id = %s AND status = 'draft'",
                       (args.by, args.release))
    conn.commit()
    print("approved" if cur.rowcount else "nothing changed (is the release a draft?)")
    return 0 if cur.rowcount else 1


def cmd_list(_: argparse.Namespace) -> int:
    conn = connect_ingest()
    for r in conn.execute("SELECT release_id::text, created_at, status, approved_by, manifest->>'release_checksum' FROM ingest.release ORDER BY created_at"):
        print(*r, sep=" | ")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="profinder_pipeline release", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--only", action="append", default=[])
    b.add_argument("--mode", choices=("additive", "insert-only"), default="additive", help="recorded in the manifest (the plan was made by `match run --mode`)")
    b.add_argument("--dry-run", action="store_true")
    for name in ("verify", "qa-status", "approve"):
        s = sub.add_parser(name)
        s.add_argument("--release", required=True)
        if name == "approve":
            s.add_argument("--by", required=True)
    sub.add_parser("list")
    args = ap.parse_args(argv)
    return {"build": cmd_build, "verify": cmd_verify, "qa-status": cmd_qa, "approve": cmd_approve, "list": cmd_list}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
