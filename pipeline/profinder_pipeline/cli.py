"""`python -m profinder_pipeline <command>`. Commands that touch a database refuse non-local hosts unless stated."""
import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .logging_setup import configure_logging
from .paths import PIPELINE_DIR
from .settings import get_settings


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="profinder_pipeline", description="ProFinder data pipeline (add-only; see pipeline/docs).")
    p.add_argument("--version", action="version", version=f"profinder_pipeline {__version__}")
    p.add_argument("--log-level", default="INFO")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("init-db", help="create/upgrade schema `ingest` (the pipeline's own tables) in PIPELINE_DATABASE_URL")
    a = sub.add_parser("app-schema", help="create the app tables in the LOCAL Docker DB from a scratch Postgres copy of schema.prisma")
    a.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("seed-snapshot", help="copy CONTENT tables (no user tables) from prisma/dev.db into the LOCAL Docker DB")
    s.add_argument("--replace", action="store_true", help="TRUNCATE the 10 content tables first (local throwaway DB only)")
    s.add_argument("--dry-run", action="store_true")
    d = sub.add_parser("doctor", help="check environment, databases, tools, scope (no secrets printed)")
    d.add_argument("--online", action="store_true", help="also call the Groq models endpoint and a .edu.cn robots.txt")
    c = sub.add_parser("check-scope", help="add-only / allowed-paths guard (same as scripts/check_scope.py)")
    c.add_argument("--base")

    sub.add_parser("registry", help="registry stage (MOE/ROR/OpenAlex/CampusChina): `registry --help` lists its commands", add_help=False)
    sub.add_parser("discover", help="discovery stage (faculty_list/admissions/scholarship targets): `discover --help`", add_help=False)
    sub.add_parser("fetch", help="fetch stage (gate, static + browser fetch, cache, faculty planner): `fetch --help`", add_help=False)
    sub.add_parser("extract", help="extraction & validation (site configs, CSS, Groq fallback, candidates): `extract --help`", add_help=False)

    t = sub.add_parser("trial-tsinghua", help="Trial A: the Iteration 0 spike on the real schemas")
    tsub = t.add_subparsers(dest="trial_command", metavar="<step>")
    tp = tsub.add_parser("parse", help="list page -> candidates.jsonl")
    tp.add_argument("--offline", action="store_true", help="use inputs/tsinghua_list.html, no network")
    tp.add_argument("--extra-email-domain", action="append", default=[], help="additional allowed email domain (repeatable)")
    tp.add_argument("--require-live", action="store_true", help="a saved file does not satisfy check B1")
    tsub.add_parser("compare", help="candidates vs ground truth -> output/trial/report.md")
    gp = tsub.add_parser("groq", help="Groq smoke test on one saved profile page")
    gp.add_argument("--html")
    tsub.add_parser("land", help="write candidates into ingest.* (local DB)")
    tsub.add_parser("explain-missing", help="show where ground-truth people we missed sit in the saved list page")
    tsub.add_parser("drop-spike", help="drop the Iteration 0 schema `spike` (local DB)")
    return p


def _init_db() -> int:
    from .db.connection import connect
    from .db.hostguard import refuse_production
    from .db.migrate import EXPECTED_TABLES, apply_migrations, ingest_tables
    url = get_settings().pipeline_database_url
    if not url:
        raise SystemExit("PIPELINE_DATABASE_URL is not set in pipeline/.env")
    host = refuse_production(url, "init-db", "ALLOW_PRODUCTION_INIT")
    print(f"database host: {host}")
    with connect(url) as conn:
        done = apply_migrations(conn)
        tables = ingest_tables(conn)
    print("applied:", ", ".join(done) if done else "nothing (already up to date)")
    missing = [t for t in EXPECTED_TABLES if t not in tables]
    print("ingest tables:", ", ".join(t for t in tables if t != "migration"))
    if missing:
        print("MISSING:", ", ".join(missing))
        return 1
    print("init-db OK (5 tables present)")
    return 0


def _passthrough(argv: list[str]):
    """`registry ...`, `discover ...` and `fetch ...` own their arguments (their own argparse), so hand them over before the main parser runs."""
    level, i = "INFO", 0
    while i < len(argv) and argv[i].startswith("-"):
        if argv[i] == "--log-level" and i + 1 < len(argv):
            level, i = argv[i + 1], i + 2
        elif argv[i].startswith("--log-level="):
            level, i = argv[i].split("=", 1)[1], i + 1
        else:
            return None
    if i < len(argv) and argv[i] in ("registry", "discover", "fetch", "extract"):
        return argv[i], argv[i + 1:], level
    return None


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    handoff = _passthrough(argv)
    if handoff:
        cmd, rest, level = handoff
        configure_logging(level)
        if cmd == "registry":
            from .registry.run import main as registry_main
            return registry_main(rest)
        if cmd == "fetch":
            from .fetch.run import main as fetch_main
            return fetch_main(rest)
        if cmd == "extract":
            from .extract.run import main as extract_main
            return extract_main(rest)
        from .discovery.run import main as discover_main
        return discover_main(rest)
    parser = _build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level)
    if not args.command:
        parser.print_help()
        return 0
    if args.command == "init-db":
        return _init_db()
    if args.command == "app-schema":
        from .db.app_schema import app_schema
        return app_schema(get_settings().app_database_url, dry_run=args.dry_run)
    if args.command == "seed-snapshot":
        from .db.seed_snapshot import seed_snapshot
        return seed_snapshot(get_settings().app_database_url, replace=args.replace, dry_run=args.dry_run)
    if args.command == "doctor":
        from .doctor import doctor
        return doctor(online=args.online)
    if args.command == "check-scope":
        cmd = [sys.executable, str(PIPELINE_DIR / "scripts" / "check_scope.py")] + (["--base", args.base] if args.base else [])
        return subprocess.call(cmd)
    if args.command == "trial-tsinghua":
        from .trial import tsinghua as t
        step = args.trial_command
        if step == "parse":
            return t.run_parse(args.offline, args.extra_email_domain, args.require_live)
        if step == "compare":
            return t.run_compare()
        if step == "groq":
            from .extract.groq_probe import run_probe
            from .paths import INPUTS_DIR
            r = run_probe(Path(args.html) if args.html else INPUTS_DIR / "tsinghua_prof1.html")
            print("valid_json:", r.get("valid_json"), "| variant:", r.get("working_variant"))
            print("usage:", r.get("usage"), "| est. cost USD:", r.get("estimated_cost_usd"))
            print("interests:", r.get("research_interests"), "| grounded:", r.get("interests_grounded"))
            print("rate-limit headers:", r.get("rate_limit_headers"))
            return 0 if r.get("valid_json") else 1
        if step == "land":
            return t.run_land()
        if step == "explain-missing":
            return t.run_explain_missing()
        if step == "drop-spike":
            return t.run_drop_spike()
        parser.parse_args(["trial-tsinghua", "--help"])
    parser.print_help()
    return 0
    