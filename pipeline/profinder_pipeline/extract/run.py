"""`extract` command: pages already in the cache -> validated candidates (tasks 4.1, 4.2, 4.4, 4.5, 4.7).
Reads only the page cache (no network, politeness is the fetch stage's job). Writes output/extract/<slug>.jsonl,
<slug>_stats.json and, unless --no-db, ingest.run + ingest.candidate (history is kept: later runs add rows, the newest run per
university is the current one). Nothing is written to app tables."""
import argparse
import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from ..fetch.cache import PageCache, url_hash as cache_url_hash
from ..paths import CONFIG_DIR, INPUTS_DIR, OUTPUT_DIR
from ..utils.text import normalise_key_text
from ..utils.urls import canonical_url, host_in_domains
from ..validate.professor_logic import Check, Context, SourcePage, evaluate_person
from .css import RawPerson, classify_href, llm_bio_text, parse_list, parse_profile, visible_text
from .sitecfg import SiteConfig, load_site_configs

OUT_DIR = OUTPUT_DIR / "extract"
PAGE_CACHE_DIR = INPUTS_DIR / "cache" / "pages"
EXTRACT_VERSION = "css-v1"


# ----------------------------------------------------------------------------------------------- inputs
def load_domain_info(university: str) -> tuple[list[str], list[str]]:
    data = yaml.safe_load((CONFIG_DIR / "domains.yml").read_text(encoding="utf-8")) or {}
    entry = (data.get("universities") or {}).get(university) or {}
    domains = list(entry.get("domains") or []) + list(entry.get("extra_domains") or [])
    emails = list(entry.get("email_domains") or []) or list(domains)
    try:
        seeds = yaml.safe_load((CONFIG_DIR / "universities.yml").read_text(encoding="utf-8")) or {}
        for u in seeds.get("universities", []):
            if u.get("name") == university:
                emails += [d for d in (u.get("extra_email_domains") or []) if d not in emails]
    except Exception:  # noqa: BLE001
        pass
    return domains, emails


def load_suppress() -> tuple[set[str], set[str]]:
    p = CONFIG_DIR / "suppress.yml"
    if not p.is_file():
        return set(), set()
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {str(e).lower() for e in d.get("emails") or []}, {str(n) for n in d.get("names") or []}


def _read_cached(cache: PageCache, url: str):
    for u in (url, canonical_url(url)):
        page = cache.read(u, allow_stale=True)
        if page is not None:
            return page
    return None


def _page_id(conn: Any, url: str) -> Optional[str]:
    if conn is None:
        return None
    for u in (url, canonical_url(url)):
        row = conn.execute("SELECT page_id::text FROM ingest.page WHERE url_hash = %s ORDER BY fetched_at DESC LIMIT 1",
                           (cache_url_hash(u),)).fetchone()
        if row:
            return row[0]
    return None


def _source_page(cache_page, url: str, domains: list[str], conn: Any) -> SourcePage:
    return SourcePage(url=url, status=cache_page.status, fetched_at=cache_page.fetched_at, text=visible_text(cache_page.html),
                      page_id=_page_id(conn, url), host_ok=host_in_domains(cache_page.final_url or url, domains))


# ----------------------------------------------------------------------------------------------- one university
def extract_university(cfg: SiteConfig, conn: Any, use_llm: bool, llm_limit: int, client: Any = None) -> dict[str, Any]:
    from ..schemas.candidate import Candidate
    from ..utils.placeholders import PlaceholderRegistry
    from ..validate.professor import to_candidate

    cache = PageCache(PAGE_CACHE_DIR)
    domains, email_domains = load_domain_info(cfg.university)
    list_cached = _read_cached(cache, cfg.list_url)
    if list_cached is None:
        raise SystemExit(f"{cfg.university}: list page is not in the cache. Run: fetch run --only \"{cfg.university}\"")
    lp = parse_list(list_cached.html, cfg, list_cached.final_url or cfg.list_url)
    list_page = _source_page(list_cached, cfg.list_url, domains, conn)

    slug = cfg.slug
    prev_stats = _read_json(OUT_DIR / f"{slug}_stats.json")
    flags: list[Check] = []
    n = len(lp.people)
    flags.append(Check("S_list_below_min", n >= cfg.min_items, False, f"{n} people, expected at least {cfg.min_items}"))
    if prev_stats and prev_stats.get("people_parsed"):
        old = int(prev_stats["people_parsed"])
        drift = abs(n - old) / max(old, 1)
        flags.append(Check("S_list_count_drift", drift <= 0.30, False, f"{old} -> {n} people since the previous run"))

    suppress_emails, suppress_names = load_suppress()
    ctx = Context(university=cfg.university, site=cfg, domains=domains, email_domains=email_domains, list_page=list_page,
                  suppress_emails=suppress_emails, suppress_names=suppress_names, university_flags=flags)
    registry = PlaceholderRegistry.from_yaml()
    now = datetime.now(timezone.utc)
    cands: list[Candidate] = []
    seen_keys: set[str] = set()
    st: Counter[str] = Counter()
    llm_used = 0
    model = getattr(client, "model", None)

    for raw in lp.people:
        profile = profile_page = None
        kind, url = classify_href(raw.profile_href, cfg.list_url, cfg.personal_url_patterns, domains)
        html = None
        if kind == "personal" and url:
            pc = _read_cached(cache, url)
            if pc is not None:
                html = pc.html
                profile_page = _source_page(pc, url, domains, conn)
                profile = parse_profile(pc.html, cfg, url)
        if cfg.profile_required and profile_page is None:
            st["pending_profile"] += 1
            continue
        llm_text: Optional[str] = None
        if (use_llm and client is not None and profile is not None and profile_page is not None and not profile.interests_lines
                and llm_used < llm_limit and cfg.profile_spec.get("llm_interests_css") and html):
            bio = llm_bio_text(html, cfg)
            if bio:
                from .llm import interests_from_text
                llm_text, _quote = interests_from_text(client, bio, profile_page.text, url or "")
                llm_used += 1
        built = evaluate_person(raw, profile, profile_page, ctx, llm_interests=llm_text)
        if built is None:
            st["no_name"] += 1
            continue
        cand = to_candidate(built, cfg.university, registry, now, model=model, prompt_version="interests-v1" if llm_text else None)
        if cand.natural_key in seen_keys:
            st["duplicate_key"] += 1
            continue
        seen_keys.add(cand.natural_key)
        cands.append(cand)
        st[cand.decision.value] += 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / f"{slug}.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for c in cands:
            fh.write(c.model_dump_json() + "\n")
    stats = {
        "university": cfg.university, "slug": slug, "extract_version": EXTRACT_VERSION, "run_at": now.isoformat(timespec="seconds"),
        "list_url": cfg.list_url, "list_status": list_page.status, "list_fetched_at": list_page.fetched_at.isoformat(timespec="seconds"),
        "raw_items": lp.raw_items, "people_parsed": n, "skipped_not_person_link": lp.skipped_not_person_link,
        "duplicate_urls": lp.duplicate_urls, "candidates": len(cands), "decisions": {k: st[k] for k in ("auto_accept", "needs_review", "rejected")},
        "pending_profile": st["pending_profile"], "no_name": st["no_name"], "duplicate_key": st["duplicate_key"],
        "llm_interest_calls": llm_used,
    }
    _write_json(OUT_DIR / f"{slug}_stats.json", stats)
    stats["_candidates"] = cands
    return stats


def _read_json(p: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except Exception:  # noqa: BLE001
        return None


def _write_json(p: Path, data: dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({k: v for k, v in data.items() if not k.startswith("_")}, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------------------------------- db
def _db_connect():
    from ..db.connection import connect
    from ..db.hostguard import refuse_production
    from ..settings import get_settings
    url = get_settings().pipeline_database_url
    print("database host:", refuse_production(url, "extract", "ALLOW_PRODUCTION_EXTRACT"))
    return connect(url)


def _selected(args: argparse.Namespace) -> list[SiteConfig]:
    cfgs = load_site_configs()
    if not args.only:
        return list(cfgs.values())
    missing = [n for n in args.only if n not in cfgs]
    if missing:
        raise SystemExit(f"no site config in config/sites/ for: {', '.join(missing)} (known: {', '.join(cfgs)})")
    return [cfgs[n] for n in args.only]


def cmd_run(args: argparse.Namespace) -> int:
    from ..db.ingest_store import finish_run, insert_candidate, start_run
    from ..schemas.enums import RunKind, RunStatus

    cfgs = _selected(args)
    client = None
    if args.llm:
        from .llm import build_client
        client = build_client()
    conn_cm = None if args.no_db else _db_connect()
    exit_code = 0
    try:
        for cfg in cfgs:
            conn = conn_cm
            stats = extract_university(cfg, conn, args.llm, args.llm_limit, client)
            cands = stats.pop("_candidates")
            d = stats["decisions"]
            print(f"[{cfg.university}] parsed {stats['people_parsed']} people -> {stats['candidates']} candidates "
                  f"(auto_accept {d['auto_accept']}, needs_review {d['needs_review']}, rejected {d['rejected']}); "
                  f"waiting for profile page: {stats['pending_profile']}; llm calls: {stats['llm_interest_calls']}")
            if conn is not None and not args.dry_run:
                run_id = start_run(conn, RunKind.extract, {"university": cfg.university, "extract_version": EXTRACT_VERSION, "llm": bool(args.llm)})
                for c in cands:
                    insert_candidate(conn, run_id, c)
                finish_run(conn, run_id, RunStatus.ok, {k: v for k, v in stats.items() if k != "university"})
                conn.commit()
                print(f"  ingest.candidate: {len(cands)} rows written (run {run_id[:8]})")
            else:
                print("  database not written (--dry-run or --no-db); JSONL written to output/extract/")
        if client is not None:
            t = client.totals
            print(f"LLM: {int(t['calls'])} calls, {int(t['prompt'])} prompt + {int(t['completion'])} completion tokens, ~USD {t['usd']:.4f}")
    finally:
        if conn_cm is not None:
            conn_cm.close()
    return exit_code


def cmd_show(args: argparse.Namespace) -> int:
    for cfg in _selected(args):
        path = OUT_DIR / f"{cfg.slug}.jsonl"
        if not path.is_file():
            print(f"{cfg.university}: no output yet, run `extract run --only \"{cfg.university}\" --no-db` first")
            continue
        rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        random.Random(7).shuffle(rows)
        print(f"== {cfg.university}: {len(rows)} candidates (random sample of {args.limit}, fixed seed)")
        for r in rows[: args.limit]:
            p = r["payload"]
            print(f"- {p.get('nameZh')} | {p.get('name')} | {p.get('position')} | {p.get('email')} | {p.get('profileUrl')} | "
                  f"{r['decision']} | areas={p.get('researchAreas')} | interests={(p.get('researchInterests') or '')[:60]}")
            failed = [c["name"] for c in r["checks"] if not c["passed"]]
            if failed:
                print("    failed checks:", ", ".join(failed))
    return 0


def cmd_clean_targets(args: argparse.Namespace) -> int:
    """Mark profile targets that are not person links (navigation pages the Iteration 3 planner picked up) as failed. Preview unless --apply."""
    total = 0
    with _db_connect() as conn:
        for cfg in _selected(args):
            cache = PageCache(PAGE_CACHE_DIR)
            lc = _read_cached(cache, cfg.list_url)
            if lc is None:
                print(f"{cfg.university}: list page not cached, skipped")
                continue
            domains, _ = load_domain_info(cfg.university)
            lp = parse_list(lc.html, cfg, lc.final_url or cfg.list_url)
            good = {canonical_url(url) for r in lp.people for k, url in [classify_href(r.profile_href, cfg.list_url, cfg.personal_url_patterns, domains)] if k == "personal" and url}
            rows = conn.execute("SELECT target_id::text, url, status FROM ingest.target WHERE university_name = %s AND kind = 'faculty_profile' "
                                "AND status IN ('pending','ok')", (cfg.university,)).fetchall()
            bad = [(tid, url, status) for tid, url, status in rows if canonical_url(url) not in good]
            print(f"{cfg.university}: {len(rows)} profile targets, {len(good)} person links on the list, {len(bad)} not person links")
            for _tid, url, status in bad[:8]:
                print(f"   {status:8} {url}")
            if args.apply:
                for tid, _u, _s in bad:
                    conn.execute("UPDATE ingest.target SET status='failed', last_error='not_a_person_link' WHERE target_id = %s::uuid", (tid,))
                conn.commit()
            total += len(bad)
    print(("marked failed: " if args.apply else "would mark failed (add --apply): ") + str(total))
    return 0


def cmd_llm_test(args: argparse.Namespace) -> int:
    """Live check of the new Groq client on 2 cached profile bios (needs GROQ_API_KEY / GROQ_MODEL in .env). Spends ~2-3K tokens."""
    from .llm import build_client, interests_from_text
    cfg = _selected(args)[0]
    cache = PageCache(PAGE_CACHE_DIR)
    lc = _read_cached(cache, cfg.list_url)
    if lc is None:
        raise SystemExit("list page not cached")
    domains, _ = load_domain_info(cfg.university)
    lp = parse_list(lc.html, cfg, lc.final_url or cfg.list_url)
    client = build_client()
    done = 0
    for r in lp.people:
        k, url = classify_href(r.profile_href, cfg.list_url, cfg.personal_url_patterns, domains)
        pc = _read_cached(cache, url) if url else None
        bio = llm_bio_text(pc.html, cfg) if pc else None
        if not bio:
            continue
        interests, quote = interests_from_text(client, bio, visible_text(pc.html), url)
        print(f"{r.name_raw}: interests={interests!r} quote={quote!r}")
        done += 1
        if done >= args.n:
            break
    t = client.totals
    print(f"calls {int(t['calls'])}, tokens {int(t['prompt'])}+{int(t['completion'])}, ~USD {t['usd']:.4f}; log: output/extract/llm_calls.jsonl")
    return 0 if done else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="profinder_pipeline extract", description="Extraction & validation (Iteration 4)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--only", action="append", help="university name (repeatable); default: every site in config/sites/")

    p = sub.add_parser("run", help="parse cached pages into candidates")
    common(p)
    p.add_argument("--no-db", action="store_true", help="write only output/extract/*.jsonl")
    p.add_argument("--dry-run", action="store_true", help="do not write ingest.candidate")
    p.add_argument("--llm", action="store_true", help="use Groq for research interests where the page has no interests section")
    p.add_argument("--llm-limit", type=int, default=60, help="max Groq calls per university per run (default 60; results are cached)")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("show", help="print a random sample of the last output")
    common(p)
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("clean-targets", help="mark non-person profile targets failed (preview unless --apply)")
    common(p)
    p.add_argument("--apply", action="store_true")
    p.set_defaults(fn=cmd_clean_targets)
    p = sub.add_parser("llm-test", help="live Groq check on cached profile bios")
    common(p)
    p.add_argument("--n", type=int, default=2)
    p.set_defaults(fn=cmd_llm_test)
    p = sub.add_parser("report", help="write output/extract/extraction_report.md")
    p.set_defaults(fn=lambda a: __import__("profinder_pipeline.extract.report", fromlist=["x"]).write_report())
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
