from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

import yaml

from ..registry.envload import PIPELINE_ROOT, load_env
from ..registry.merge import load_seed
from ..registry.textnorm import norm_en, registrable_domain
from ..registry.textnorm import host_allowed, host_of
from .crawl import KINDS, Discoverer, Found, Result, UniInput
from .scoring import score_link
from .fetcher import PoliteFetcher
from .search import QUERIES, SearchClient
from .store import (DELETE_BY_ID, DELETE_REJECTED, LIST_PENDING_DUPES, close_run, open_run, plan_dedupe, result_to_dict, save_result)

INPUTS, CACHE = PIPELINE_ROOT / "inputs", PIPELINE_ROOT / "inputs" / "cache"
OUT = PIPELINE_ROOT / "output" / "discovery"
SEED, DOMAINS = PIPELINE_ROOT / "config" / "universities.yml", PIPELINE_ROOT / "config" / "domains.yml"
MERGED = PIPELINE_ROOT / "output" / "registry" / "universities_merged.json"
RESULTS, SUMMARY, REPORT = OUT / "results.json", OUT / "targets_summary.json", OUT / "discovery_report.csv"


def load_inputs(only: list[str] | None = None) -> list[UniInput]:
    seeds = load_seed(SEED)
    if not DOMAINS.is_file():
        raise SystemExit("config/domains.yml missing: run `python -m profinder_pipeline registry build --write-domains` first")
    doms = yaml.safe_load(DOMAINS.read_text(encoding="utf-8"))["universities"]
    merged = {m["name"]: m for m in json.loads(MERGED.read_text(encoding="utf-8"))} if MERGED.is_file() else {}
    want = {norm_en(x) for x in only} if only else None
    out = []
    for s in seeds:
        if want is not None and norm_en(s.name) not in want:
            continue
        d = doms.get(s.name) or {}
        domains = list(d.get("domains") or [])
        if not domains:
            print(f"skip {s.name}: no allowed domain")
            continue
        m = merged.get(s.name, {})
        regs = [x for x in dict.fromkeys(registrable_domain(x) for x in domains) if x]
        starts = list(dict.fromkeys([u for u in [m.get("official_url")] if u] + [f"https://www.{r}/" for r in regs] + [f"https://{r}/" for r in regs]))
        out.append(UniInput(s.name, m.get("existing_id"), starts, domains, {k: list(v) for k, v in s.seed_urls if v}))
    return out


def _load_results() -> dict:
    return json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.is_file() else {}


def _save_results(data: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def merge_retry(prev: dict, new: dict) -> dict:
    """A retry only attempts the kinds that were missing; keep everything found before and add what is new."""
    got = {f["kind"]: f for f in prev["found"]}
    got.update({f["kind"]: f for f in new["found"]})
    out = dict(new)
    out["found"] = [got[k] for k in KINDS if k in got]
    out["missing"] = [k for k in KINDS if k not in got]
    return out


def _summary_text(r: dict) -> str:
    got = {f["kind"]: f["method"] + ("" if f["validated"] in (True, None) else "?") for f in r["found"]}
    return "|".join(f"{k}:{got.get(k, 'no_page_found')}" for k in KINDS)


def write_reports(results: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps({n: _summary_text(r) for n, r in results.items()}, ensure_ascii=False, indent=1), encoding="utf-8")
    cols = ["university", "kind", "outcome", "method", "url", "validated", "score", "via", "pages_fetched", "cache_hits", "notes", "offdomain_candidates"]
    with REPORT.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for n, r in results.items():
            by = {f["kind"]: f for f in r["found"]}
            for k in KINDS:
                f = by.get(k)
                w.writerow({
                    "university": n, "kind": k, "outcome": ("unvalidated" if f and f["validated"] is False else "found") if f else "no_page_found",
                    "method": f["method"] if f else "", "url": f["url"] if f else r.get("start_url") or "", "validated": "" if not f or f["validated"] is None else str(f["validated"]).lower(),
                    "score": f["score"] if f else "", "via": (f or {}).get("via") or "", "pages_fetched": r["pages_fetched"], "cache_hits": r["cache_hits"],
                    "notes": "; ".join([*(r["notes"] if not f else []), *([f["note"]] if f and f["note"] else []),
                                        *([f"blocked_by_robots:{len(r['blocked'])}"] if r["blocked"] and not f else []),
                                        *([f"errors:{' '.join(u.rsplit('/', 1)[-1][:20] + '=' + e for u, e in r['errors'][:3])}"] if r["errors"] and not f else [])]),
                    "offdomain_candidates": " ".join(r["offdomain"][:3]) if not f else "",
                })


def _search_client(provider: str, client, max_calls: int):
    key = {"tavily": os.environ.get("TAVILY_API_KEY"), "serper": os.environ.get("SERPER_API_KEY")}
    if provider == "auto":
        provider = "tavily" if key["tavily"] else ("serper" if key["serper"] else "none")
    if provider == "none":
        return None
    if not key.get(provider):
        raise SystemExit(f"{provider.upper()}_API_KEY is not set in pipeline/.env")
    return SearchClient(provider, key[provider], client, CACHE, max_calls)


def _client():
    import httpx

    from ..fetch.basic import USER_AGENT

    return httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30.0)


def cmd_run(a) -> int:
    load_env()
    unis = load_inputs(a.only)
    results = _load_results()
    if a.resume:
        unis = [u for u in unis if u.name not in results]
    if a.retry_missing:
        unis = [u for u in unis if u.name in results and results[u.name]["missing"]]
    unis = unis[a.start:][: a.limit or None]
    print(f"universities to process: {len(unis)}  (page budget {a.budget} each; dry-run={a.dry_run}; offline={a.offline})")
    client = _client()
    fetcher = PoliteFetcher(client, CACHE / "pages", offline=a.offline)
    search = None if a.offline else _search_client(a.search, client, a.max_search_calls)
    disc = Discoverer(fetcher, search)

    conn = cur = run_id = None
    if not a.dry_run:
        from ..db.connection import connect
        from ..db.hostguard import refuse_production
        from ..settings import get_settings

        url = get_settings().pipeline_database_url
        if not url:
            raise SystemExit("PIPELINE_DATABASE_URL is not set in pipeline/.env")
        print(f"database host: {refuse_production(url, 'discover', 'ALLOW_PRODUCTION_DISCOVER')}")
        conn = connect(url).__enter__()
        cur = conn.cursor()
        run_id = open_run(cur, {"only": a.only, "budget": a.budget, "search": search.provider if search else "none", "n": len(unis)})
        conn.commit()
    totals = {"found": 0, "miss": 0, "unvalidated": 0}
    status = "ok"
    try:
        for i, u in enumerate(unis, 1):
            prev = results.get(u.name)
            kinds = tuple(prev["missing"]) if (a.retry_missing and prev) else KINDS
            res: Result = disc.run(u, a.budget, kinds)
            if cur is not None:
                save_result(cur, res)
                conn.commit()
            results[u.name] = merge_retry(prev, result_to_dict(res)) if (a.retry_missing and prev) else result_to_dict(res)
            res = Result(**{**results[u.name], "found": [Found(**f) for f in results[u.name]["found"]], "errors": [tuple(e) for e in results[u.name]["errors"]]})
            _save_results(results)
            totals["found"] += len(res.found)
            totals["miss"] += len(res.missing)
            totals["unvalidated"] += sum(1 for f in res.found if f.validated is False)
            got = {f.kind: f.method + ("?" if f.validated is False else "") for f in res.found}
            print(f"[{i}/{len(unis)}] {u.name}: " + " ".join(f"{k}={got.get(k, '-')}" for k in KINDS) + f"  (pages {res.pages_fetched}, cached {res.cache_hits})"
                  + (f"  notes: {'; '.join(res.notes)}" if res.notes else ""))
    except KeyboardInterrupt:
        status = "partial"
        print("interrupted: progress is saved; continue with --resume")
    finally:
        write_reports(results)
        if cur is not None:
            close_run(cur, run_id, {**totals, "search_calls": search.calls if search else 0}, status)
            conn.commit()
            conn.__exit__(None, None, None)
    print(f"found {totals['found']} targets ({totals['unvalidated']} unvalidated), missing {totals['miss']}; search calls used: {search.calls if search else 0}")
    print(f"wrote {REPORT.relative_to(PIPELINE_ROOT)} and {SUMMARY.relative_to(PIPELINE_ROOT)}")
    return 0 if status == "ok" else 1


def revalidate_results(results: dict, unis: dict, fetcher, disc) -> list[tuple[str, str, str, str]]:
    """Re-check every VALIDATED, non-seed target against the cached page with the current rules. Rejected ones move from
    `found` to `missing`. Targets whose page is not cached (cannot be judged), seeds and flagged-unvalidated ones are kept."""
    dropped = []
    for name, r in results.items():
        u = unis.get(name)
        if u is None:
            continue
        fetcher.begin(u.domains, 0)
        keep = []
        for fd in r["found"]:
            page = None if (fd["method"] == "seed" or fd["validated"] is not True) else fetcher.get(fd["url"])
            why = None if page is None else disc._why(fd["kind"], page, from_search=fd["method"] == "search")
            if why is None:
                keep.append(fd)
            else:
                dropped.append((name, fd["kind"], fd["url"], why))
        if len(keep) != len(r["found"]):
            r["notes"] = [*r["notes"], "revalidated_rejected:" + ",".join(k for n, k, _u, _w in dropped if n == name)]
        r["found"] = keep
        r["missing"] = [k for k in KINDS if k not in {x["kind"] for x in keep}]
    return dropped


def _db():
    from ..db.connection import connect
    from ..db.hostguard import refuse_production
    from ..settings import get_settings

    url = get_settings().pipeline_database_url
    if not url:
        raise SystemExit("PIPELINE_DATABASE_URL is not set in pipeline/.env")
    print(f"database host: {refuse_production(url, 'discover', 'ALLOW_PRODUCTION_DISCOVER')}")
    ctx = connect(url)
    return ctx, ctx.__enter__()


def cmd_revalidate(a) -> int:
    """Offline (page cache only, no network, no search credits): drop targets that the current, stricter rules reject."""
    load_env()
    unis = {u.name: u for u in load_inputs(None)}
    results = _load_results()
    fetcher = PoliteFetcher(None, CACHE / "pages", offline=True)
    dropped = revalidate_results(results, unis, fetcher, Discoverer(fetcher, None))
    for n, k, u, why in dropped:
        print(f"reject {n} | {k} | {u} | reason: {why}")
    print(f"{len(dropped)} target(s) rejected; {sum(1 for r in results.values() if r['missing'])} universities now have a missing kind")
    if a.dry_run or not dropped:
        print("(dry-run: nothing written)" if a.dry_run else "")
        return 0
    ctx, conn = _db()
    try:
        cur = conn.cursor()
        for n, k, u, _why in dropped:
            cur.execute(DELETE_REJECTED, (n, k, u))
        conn.commit()
    finally:
        ctx.__exit__(None, None, None)
    _save_results(results)
    write_reports(results)
    print("database and results.json updated; now run: discover run --retry-missing")
    return 0


def cmd_dedupe(a) -> int:
    """One pending target per university+kind (seed > validated > link_follow > search). Only ingest.target rows with status pending."""
    load_env()
    ctx, conn = _db()
    try:
        cur = conn.cursor()
        cur.execute(LIST_PENDING_DUPES)
        rows = [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in cur.fetchall()]
        keep, drop = plan_dedupe([(r[0], r[1], str(r[2]), r[3], r[4], r[5]) for r in rows])
        for r in drop:
            print(f"drop {r[0]} | {r[1]} | {r[4]} | {r[3]}")
        print(f"{len(keep)} university/kind group(s) with duplicates, {len(drop)} extra row(s)" + ("" if a.apply else "  (preview only: add --apply to delete them)"))
        if a.apply:
            for r in drop:
                cur.execute(DELETE_BY_ID, (r[2],))
            conn.commit()
    finally:
        ctx.__exit__(None, None, None)
    return 0


def explain_lines(page, domains, top: int = 6) -> list[str]:
    out = [f"links on page: {len(page.links)}"]
    for kind in ("department", "dept_index", "faculty_list", "admissions", "scholarship"):
        out.append(f"  {kind}:")
        for sc, l in sorted(((score_link(kind, l.text, l.url), l) for l in page.links), key=lambda t: -t[0])[:top]:
            if sc <= 0:
                break
            out.append(f"    {sc:5.1f} {'on ' if host_allowed(host_of(l.url), domains) else 'OFF'} {l.text[:36]!r} {l.url[:90]}")
    return out


def cmd_explain(a) -> int:
    """Why was nothing found? Reads ONLY the page cache (no network): top-scored links of the cached homepage per kind."""
    unis = load_inputs(a.only)
    f = PoliteFetcher(None, CACHE / "pages", offline=True)
    for u in unis:
        f.begin(u.domains, 0)
        page = next((p for p in (f.get(s) for s in u.start_urls) if p), None)
        print(f"== {u.name}")
        if page is None:
            print("  homepage not in the page cache: it was unreachable in the last run (bot protection, timeout or robots.txt)")
            continue
        print(f"  homepage: {page.final_url}")
        print("\n".join(explain_lines(page, u.domains, a.top)))
    return 0


def cmd_report(a) -> int:
    results = _load_results()
    write_reports(results)
    print(f"report rebuilt for {len(results)} universities -> {REPORT.relative_to(PIPELINE_ROOT)}")
    return 0


def cmd_probe_search(a) -> int:
    load_env()
    sc = _search_client(a.provider, _client(), 5)
    if sc is None:
        raise SystemExit("no search key configured")
    data = sc._request("Tsinghua University international students admissions", ["tsinghua.edu.cn"])
    p = CACHE / "search" / sc.provider / "probe.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    from .search import parse_serper, parse_tavily

    hits = (parse_tavily if sc.provider == "tavily" else parse_serper)(data)
    print(f"{sc.provider}: top-level keys {sorted(data)} | parsed hits: {len(hits)}")
    for h in hits[:4]:
        print("  ", h.url, "|", h.title[:60])
    return 0 if hits else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="profinder_pipeline discover", description="Discovery stage (task 2.5): faculty_list / admissions / scholarship targets")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="discover targets and write ingest.target (unless --dry-run)")
    r.add_argument("--only", action="append", help="university name (repeatable)")
    r.add_argument("--limit", type=int)
    r.add_argument("--start", type=int, default=0)
    r.add_argument("--budget", type=int, default=30, help="max network page fetches per university")
    r.add_argument("--search", choices=["auto", "tavily", "serper", "none"], default="auto")
    r.add_argument("--max-search-calls", type=int, default=150)
    r.add_argument("--dry-run", action="store_true", help="do not write ingest.target / ingest.run")
    r.add_argument("--offline", action="store_true", help="use cached pages only, no network, no search")
    r.add_argument("--resume", action="store_true", help="skip universities already in output/discovery/results.json")
    r.add_argument("--retry-missing", action="store_true", help="re-run only universities that still miss a kind")
    e = sub.add_parser("explain", help="show the top-scored links of a cached homepage per kind (no network): why was nothing found?")
    e.add_argument("--only", action="append", required=True)
    e.add_argument("--top", type=int, default=6)
    v = sub.add_parser("revalidate", help="offline: re-check found targets with the current rules; rejected ones become missing")
    v.add_argument("--dry-run", action="store_true")
    dd = sub.add_parser("dedupe", help="remove duplicate pending targets (one per university+kind); preview unless --apply")
    dd.add_argument("--apply", action="store_true")
    sub.add_parser("report", help="rebuild discovery_report.csv and targets_summary.json from results.json")
    p = sub.add_parser("probe-search", help="one search call to check the provider response shape")
    p.add_argument("--provider", choices=["tavily", "serper"], default="tavily")
    a = ap.parse_args(argv)
    import logging
    logging.getLogger("httpx").setLevel(logging.WARNING)   # one INFO line per request floods the terminal
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    return {"run": cmd_run, "report": cmd_report, "probe-search": cmd_probe_search, "explain": cmd_explain, "revalidate": cmd_revalidate, "dedupe": cmd_dedupe}[a.cmd](a)
