"""`python -m profinder_pipeline load ...` and `... rollback ...`
  load --release ID [--apply] [--lock-policy strict|import-ok]    dry-run is the default (read-only connection, no write at all)
  rollback --release ID [--apply]
Real writes need ALL of: --apply, CONFIRM_LOAD=<release id> (CONFIRM_ROLLBACK for rollback), an APPROVED release (a draft is only accepted for a
local database with ALLOW_DRAFT_LOAD=<release id>), verified checksums, and for hosts containing prod/production ALLOW_PRODUCTION_LOAD=<release id>.
The whole load is ONE transaction; it is committed only if a second plan inside it is empty and the user tables are unchanged."""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from ..paths import OUTPUT_DIR
from ..release.run import _folder, connect_ingest, verify_folder
from ..utils.placeholders import PlaceholderRegistry
from .db import PgDb
from .loader import LoadAbort, read_release, run_load
from .rollback import run_rollback


def _resolve(release: str) -> tuple[str, Any]:
    folder = _folder(release)
    man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    rid = man["release_id"]
    if not rid.startswith(release) and not release.startswith(rid[:6]):
        raise SystemExit(f"release {release} does not match the folder {folder.name}")
    return rid, folder


def _app_connection(apply: bool, rid: str, confirm_var: str):
    from ..db.connection import connect
    from ..db.hostguard import url_host
    from ..settings import get_settings
    url = get_settings().app_database_url
    if not url:
        raise SystemExit("APP_DATABASE_URL is not set in pipeline/.env")
    host = url_host(url)
    low = host.lower()
    if any(w in low for w in ("prod", "production")) and os.environ.get("ALLOW_PRODUCTION_LOAD") != rid:
        raise SystemExit(f"refusing host {host}: it looks like production (the owner sets ALLOW_PRODUCTION_LOAD=<release id>)")
    if apply and os.environ.get(confirm_var) != rid:
        raise SystemExit(f"real writes need {confirm_var}={rid} in the environment (plus --apply)")
    print(("app host (WRITE): " if apply else "app host (read-only): ") + host)
    conn = connect(url)
    if not apply:
        conn.read_only = True
    return conn, host


def _write_report(kind: str, rid: str, result: dict[str, Any]) -> None:
    out = OUTPUT_DIR / "load"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{rid[:6]}_{kind}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def cmd_load(args: argparse.Namespace) -> int:
    rid, folder = _resolve(args.release)
    problems = verify_folder(folder)
    if problems:
        print("release folder does not verify:", problems)
        return 1
    ing = connect_ingest()
    row = ing.execute("SELECT status FROM ingest.release WHERE release_id = %s::uuid", (rid,)).fetchone()
    status = row[0] if row else None
    if args.apply and status != "approved":
        local_ok = os.environ.get("ALLOW_DRAFT_LOAD") == rid
        if not (status == "loaded" or (status == "draft" and local_ok)):
            print(f"release status is {status!r}: only an approved release can be applied (a local test of a draft needs ALLOW_DRAFT_LOAD={rid})")
            return 1
    app, host = _app_connection(args.apply, rid, "CONFIRM_LOAD")
    from ..db.hostguard import is_local
    from ..settings import get_settings
    if args.apply and status == "draft" and not is_local(get_settings().app_database_url or ""):
        print("a draft release can only be applied to a local database")
        return 1
    db = PgDb(app)
    try:
        result = run_load(db, read_release(folder), rid, PlaceholderRegistry.from_yaml(), args.apply, datetime.now(timezone.utc), args.lock_policy)
        if args.apply:
            db.commit()
        else:
            db.rollback()
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
    print("user tables:", result["user_tables"])
    _write_report("applied" if args.apply else "dryrun", rid, result)
    if args.apply:
        ing.execute("UPDATE ingest.release SET status = 'loaded', loaded_to = %s, loaded_at = now() WHERE release_id = %s::uuid", (host, rid))
        ing.commit()
        print("LOADED. done:", json.dumps(result["done"]))
    else:
        print("dry-run only: nothing was written. To apply: set CONFIRM_LOAD=<release id> and add --apply")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    rid, _ = _resolve(args.release)
    app, host = _app_connection(args.apply, rid, "CONFIRM_ROLLBACK")
    db = PgDb(app)
    try:
        result = run_rollback(db, rid, args.apply)
        db.commit() if args.apply else db.rollback()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"ROLLBACK ABORTED, nothing was changed: {type(exc).__name__}: {exc}")
        return 1
    plan = result["plan"]
    print("plan:", json.dumps(plan["summary"], ensure_ascii=False))
    for s in plan["skipped"][:20]:
        print("  kept:", s)
    _write_report("rollback_applied" if args.apply else "rollback_dryrun", rid, result)
    if args.apply:
        ing = connect_ingest()
        ing.execute("UPDATE ingest.release SET status = 'abandoned', loaded_to = NULL, loaded_at = NULL WHERE release_id = %s::uuid", (rid,))
        ing.commit()
        print("ROLLED BACK. done:", json.dumps(result["done"]))
    else:
        print("dry-run only. To apply: set CONFIRM_ROLLBACK=<release id> and add --apply")
    return 0


def main(argv: Optional[list[str]] = None, prog: str = "load") -> int:
    ap = argparse.ArgumentParser(prog=f"profinder_pipeline {prog}", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--release", required=True)
    ap.add_argument("--apply", action="store_true")
    if prog == "load":
        ap.add_argument("--lock-policy", choices=("strict", "import-ok"), default="strict")
        ap.add_argument("--target", choices=("app",), default="app")
    args = ap.parse_args(argv)
    return (cmd_load if prog == "load" else cmd_rollback)(args)


def rollback_main(argv: Optional[list[str]] = None) -> int:
    return main(argv, prog="rollback")


if __name__ == "__main__":
    sys.exit(main())
