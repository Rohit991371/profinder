"""`python -m profinder_pipeline fetch <command>` (task 3.6).

  run      fetch faculty lists (+ pagination) and profile pages for chosen universities, fill ingest.target/page, write the report
  probe    fetch ONE url through the full engine (cache, gate, optional browser) and print what came back
  inspect  parse a SAVED html file (no network): people links, pagination, markdown size
  report   rebuild output/fetch/fetch_report.csv and coverage.md from output/fetch/results.json
  doctor   check playwright/chromium, domains config, proxy variable (name only)"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..paths import INPUTS_DIR, OUTPUT_DIR, PIPELINE_DIR
from ..settings import get_settings
from .cache import PageCache
from .clean import html_to_markdown
from .engine import FetchEngine, FetchedPage
from .gate import Gate, RetryPolicy, Throttle
from .hints import Hints, load_allowed_domains, norm_name
from .plan import extract_people, find_pagination, plan_faculty
from .render import BrowserRenderer
from .static import StaticFetcher, make_client

CONFIG_DIR = PIPELINE_DIR / "config"
CACHE_DIR = INPUTS_DIR / "cache" / "pages"
OUT_DIR = OUTPUT_DIR / "fetch"
TRIAL = ["Tsinghua University", "Shanghai Jiao Tong University", "East China Normal University"]


def _utf8() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


def build_engine(args, domains_hint=None) -> FetchEngine:
    st = get_settings()
    throttle = Throttle(delay=args.delay)
    static = StaticFetcher(make_client(st.proxy_url), throttle, RetryPolicy())
    renderer = None
    if not getattr(args, "no_render", False) and not getattr(args, "offline", False):
        ok, _ = BrowserRenderer.installed()
        renderer = BrowserRenderer(st.proxy_url) if ok else None
    holder: dict = {}
    gate = Gate(lambda base: holder["engine"].read_robots(base))     # robots reading uses the engine's static client + browser fallback
    engine = FetchEngine(PageCache(CACHE_DIR), static, gate, renderer, Hints.load(CONFIG_DIR / "fetch_hints.yml"),
                         offline=getattr(args, "offline", False))
    holder["engine"] = engine
    return engine


# ---------------- run ----------------
def _db_connect():
    """-> context manager yielding the connection (same call style as `init-db`)."""
    from ..db.connection import connect
    from ..db.hostguard import refuse_production
    url = get_settings().pipeline_database_url
    if not url:
        raise SystemExit("PIPELINE_DATABASE_URL is not set in pipeline/.env")
    print("database host:", refuse_production(url, "fetch", "ALLOW_PRODUCTION_FETCH"))
    return connect(url)


def _target_status(plan, first_page) -> tuple:
    if plan.kind == "faculty_list":
        return "ok", None
    if plan.kind == "not_a_faculty_list":
        return "failed", "not_a_faculty_list (%d person links)" % len(plan.profile_urls)
    err = plan.first_error or "unknown"
    return ("blocked" if err.startswith("robots_") else "failed"), err


def cmd_run(args) -> int:
    with _db_connect() as conn:
        return _run(args, conn)


def _run(args, conn) -> int:
    domains_by_name = load_allowed_domains(CONFIG_DIR)
    names = args.only or (TRIAL if args.trial else None)
    use_db = not args.dry_run
    from . import store
    wanted = [n for n in (names or [])]
    targets = store.load_targets(conn, wanted or None)
    if not targets:
        print("No pending/ok/failed faculty_list targets found for", wanted or "any university")
        return 1
    if args.limit:
        keep = list(dict.fromkeys(t["university_name"] for t in targets))[: args.limit]
        targets = [t for t in targets if t["university_name"] in keep]
    engine = build_engine(args)
    if engine.renderer is None and not args.offline and not args.no_render:
        print("note: playwright is not installed, so the browser fallback is OFF (see `fetch doctor`)")
    run_id = store.start_run(conn, {"names": wanted, "budget": args.budget, "dry_run": args.dry_run, "offline": args.offline}) if use_db else None
    results = _load_results()
    totals = Counter()
    try:
        for uni in dict.fromkeys(t["university_name"] for t in targets):
            doms = list(dict.fromkeys(domains_by_name.get(norm_name(uni), []) + list(args.allow_domain or [])))
            if not doms:
                print(f"[{uni}] no allowed domains in config/domains.yml or universities.yml - skipped (use --allow-domain)")
                continue
            engine.begin(doms, args.budget)
            entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "domains": doms, "targets": []}
            for t in [x for x in targets if x["university_name"] == uni]:
                entry["targets"].append(_run_target(engine, conn if use_db else None, t, args, totals))
            entry["budget"] = {"limit": args.budget, "network_fetches": engine.gate.used, **engine.stats}
            results[uni] = entry
            _save_results(results)
            print(f"[{uni}] network={engine.gate.used}/{args.budget} cache_hits={engine.stats['cache_hits']} "
                  f"rendered={engine.stats['rendered']} errors={engine.stats['errors']}")
        if use_db:
            store.finish_run(conn, run_id, dict(totals), "ok")
    finally:
        engine.close()
    write_reports(results)
    print("report:", OUT_DIR / "fetch_report.csv", "|", OUT_DIR / "coverage.md")
    return 0


def _run_target(engine: FetchEngine, conn, t: dict, args, totals: Counter) -> dict:
    from . import store
    plan = plan_faculty(engine, t["url"], args.max_list_pages)
    first = plan.list_pages[0] if plan.list_pages else {}
    row = {"list_url": t["url"], "kind": plan.kind, "list_pages": len(plan.list_pages), "people_with_profile": len(plan.profile_urls),
           "people_without_link": plan.no_link, "off_domain_links": plan.off_domain, "flags": plan.flags,
           "render_mode": first.get("render_mode"), "first_error": plan.first_error, "profiles_planned": len(plan.profile_urls),
           "profiles_fetched": 0, "profiles_cached": 0, "profiles_failed": 0, "profile_errors": {}}
    if conn:
        for lp in plan.list_pages:                      # list/pagination pages belong to the list target
            store.persist_page(conn, lp["fp"], t["target_id"])
    status, err = _target_status(plan, first)
    if conn and plan.kind != "list_incomplete":
        store.update_target(conn, t["target_id"], status=status, robots_status=_robots_of(engine, t["url"]), error=err,
                            parser_note={"render": first.get("render_mode"), "list_pages": len(plan.list_pages),
                                         "people": len(plan.profile_urls), "fetch_flags": plan.flags})
    print(f"  list {t['url']} -> {plan.kind}: {len(plan.profile_urls)} profile links, {len(plan.list_pages)} list page(s)"
          f"{' [' + ','.join(plan.flags) + ']' if plan.flags else ''}{' error=' + plan.first_error if plan.first_error else ''}")
    if plan.kind not in ("faculty_list", "list_incomplete") or args.no_profiles:
        return row
    ids = store.upsert_profile_targets(conn, t["university_name"], plan.profile_urls, t["url"]) if conn else {}
    errors: Counter = Counter()
    for u in plan.profile_urls:
        fp: FetchedPage = engine.get(u, "auto")
        if fp.error == "budget":
            errors["budget"] += 1
            break
        if conn:
            store.persist_page(conn, fp, ids.get(u))
            if u not in ids:
                pass
            elif fp.ok:
                store.update_target(conn, ids[u], status="ok", robots_status="allowed", error=None,
                                    parser_note={"render": fp.render_mode})
            elif fp.error not in ("budget", "offline_miss"):
                store.update_target(conn, ids[u], status="blocked" if fp.error.startswith("robots_") else "failed",
                                    robots_status=fp.robots_status, error=fp.error, parser_note={})
        if fp.ok:
            row["profiles_cached" if fp.from_cache else "profiles_fetched"] += 1
        else:
            row["profiles_failed"] += 1
            errors[fp.error] += 1
    row["profile_errors"] = dict(errors)
    row["profiles_not_attempted"] = len(plan.profile_urls) - row["profiles_fetched"] - row["profiles_cached"] - row["profiles_failed"]
    totals["profiles_fetched"] += row["profiles_fetched"]
    totals["profiles_cached"] += row["profiles_cached"]
    return row


def _robots_of(engine: FetchEngine, url: str) -> str:
    return engine.gate.robots_cached(url)


# ---------------- results / report ----------------
def _load_results() -> dict:
    p = OUT_DIR / "results.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def _save_results(results: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


def write_reports(results: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cols = ["university", "list_url", "kind", "render_mode", "list_pages", "people_with_profile", "people_without_link",
            "off_domain_links", "profiles_planned", "profiles_fetched", "profiles_cached", "profiles_failed",
            "profiles_not_attempted", "profile_errors", "flags", "first_error", "network_fetches", "cache_hits"]
    with open(OUT_DIR / "fetch_report.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        for uni, e in results.items():
            for t in e["targets"]:
                w.writerow({"university": uni, **t, "profile_errors": json.dumps(t.get("profile_errors", {}), ensure_ascii=False),
                            "flags": ",".join(t.get("flags", [])), "network_fetches": e["budget"]["network_fetches"],
                            "cache_hits": e["budget"]["cache_hits"]})
    lines = ["# Fetch coverage", "", "| university | list | kind | mode | pages | profile links | fetched | cached | failed | not attempted |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for uni, e in results.items():
        for t in e["targets"]:
            lines.append(f"| {uni} | {t['list_url']} | {t['kind']} | {t.get('render_mode') or '-'} | {t['list_pages']} | "
                         f"{t['people_with_profile']} | {t.get('profiles_fetched', 0)} | {t.get('profiles_cached', 0)} | "
                         f"{t.get('profiles_failed', 0)} | {t.get('profiles_not_attempted', '-')} |")
    (OUT_DIR / "coverage.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------- probe / inspect / doctor ----------------
def cmd_probe(args) -> int:
    from ..registry.textnorm import host_of
    engine = build_engine(args)
    host = host_of(args.url)
    engine.begin(args.allow_domain or [host], 10)
    try:
        fp = engine.get(args.url, "render" if args.render else "auto", args.wait_selector)
    finally:
        engine.close()
    print("ok:", fp.ok, "| error:", fp.error, "| status:", fp.status, "| mode:", fp.render_mode, "| from_cache:", fp.from_cache)
    print("notes:", fp.notes, "| robots:", fp.robots_status)
    if fp.ok:
        people = extract_people(fp.html, fp.final_url or fp.url, engine.domains)
        pg = find_pagination(fp.html, fp.final_url or fp.url)
        print(f"html {len(fp.html)} chars | markdown {len(fp.markdown)} chars | title: {fp.title}")
        print(f"person links: {len([p for p in people.people if p.url])} (no link: {people.no_link}, off-domain: {people.off_domain})")
        print("pagination: next=", pg.next_url, "numbered=", len(pg.numbered), "letters=", len(pg.letters), "js=", pg.js_pagination)
        print(fp.markdown[:600])
    return 0 if fp.ok else 1


def cmd_inspect(args) -> int:
    from ..registry.textnorm import host_of
    from .static import decode_html
    raw = Path(args.html).read_bytes()
    html, enc = decode_html(raw)
    base = args.url or "https://example.invalid/"
    doms = args.allow_domain or ([host_of(base)] if args.url else [])
    c = html_to_markdown(html, base)
    people = extract_people(html, base, doms) if doms else None
    pg = find_pagination(html, base)
    print(f"{args.html}: {len(raw)} bytes, encoding {enc}, title: {c.title}")
    print(f"markdown {c.chars_after} chars (visible before cleaning: {c.chars_before}, fallback: {c.fallback})")
    if people is not None:
        print(f"person links: {len([p for p in people.people if p.url])} | no link: {people.no_link} | off-domain: {people.off_domain}")
        for p in people.people[:8]:
            print("   ", p.name, "->", p.url)
    else:
        print("(pass --url and/or --allow-domain to count person links)")
    print("pagination: next=", pg.next_url, "| numbered:", len(pg.numbered), "| letters:", len(pg.letters), "| js:", pg.js_pagination)
    return 0


def cmd_doctor(args) -> int:
    ok, msg = BrowserRenderer.installed()
    print("playwright:", msg)
    if ok and args.launch:
        r = BrowserRenderer(get_settings().proxy_url)
        res = r.render("about:blank", None, 100)
        r.close()
        print("chromium launch:", "OK" if not res.error else f"FAILED ({res.error}) - run `playwright install chromium`")
    doms = load_allowed_domains(CONFIG_DIR)
    print(f"allowed-domain config: {len(doms)} universities, {sum(1 for v in doms.values() if v)} with >= 1 domain")
    print("PROXY_URL set:", bool(get_settings().proxy_url))
    print("cache dir:", CACHE_DIR, "| files:", len(list(CACHE_DIR.glob('*.html'))) if CACHE_DIR.is_dir() else 0)
    return 0


def main(argv) -> int:
    _utf8()
    import logging
    for noisy in ("httpx", "httpcore"):                       # one INFO line per request flooded the terminal
        logging.getLogger(noisy).setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="profinder_pipeline fetch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", metavar="<command>")

    def common(sp):
        sp.add_argument("--delay", type=float, default=2.5, help="seconds between requests to one host (default 2.5)")
        sp.add_argument("--no-render", action="store_true", help="never start the browser")
        sp.add_argument("--allow-domain", action="append", help="extra allowed domain (repeatable)")

    r = sub.add_parser("run", help="fetch faculty lists and profile pages")
    common(r)
    r.add_argument("--only", action="append", help="university name (repeatable)")
    r.add_argument("--trial", action="store_true", help="the three trial universities A/B/C")
    r.add_argument("--limit", type=int, help="first N universities")
    r.add_argument("--budget", type=int, default=40, help="network fetches per university per run (default 40; cache hits are free)")
    r.add_argument("--max-list-pages", type=int, default=40)
    r.add_argument("--no-profiles", action="store_true", help="list pages only")
    r.add_argument("--offline", action="store_true", help="cache only, no network")
    r.add_argument("--dry-run", action="store_true", help="no database writes (disk cache still written)")
    pr = sub.add_parser("probe", help="fetch one URL through the full engine")
    common(pr)
    pr.add_argument("url")
    pr.add_argument("--render", action="store_true")
    pr.add_argument("--wait-selector")
    pr.add_argument("--offline", action="store_true")
    ins = sub.add_parser("inspect", help="parse a saved html file (no network)")
    ins.add_argument("--html", required=True)
    ins.add_argument("--url", help="the page's real URL (for relative links)")
    ins.add_argument("--allow-domain", action="append")
    sub.add_parser("report", help="rebuild the report files from results.json")
    d = sub.add_parser("doctor", help="check the fetch environment")
    d.add_argument("--launch", action="store_true", help="also start Chromium once")
    args = p.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "probe":
        return cmd_probe(args)
    if args.cmd == "inspect":
        return cmd_inspect(args)
    if args.cmd == "report":
        write_reports(_load_results())
        print("written:", OUT_DIR)
        return 0
    if args.cmd == "doctor":
        return cmd_doctor(args)
    p.print_help()
    return 0