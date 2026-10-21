from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import openalex as oa
from . import ror as rorm
from .campuschina import load_campuschina
from .domains import build_domains, dump_domains
from .envload import PIPELINE_ROOT, load_env
from .merge import Existing, load_seed, merge_all
from .moe import load_moe
from .report import diagnose, summary, write_report
from .textnorm import norm_en
from .urlcheck import check_urls, load_checks

INPUTS = PIPELINE_ROOT / "inputs"
CACHE = INPUTS / "cache"
OUT = PIPELINE_ROOT / "output" / "registry"
SEED = PIPELINE_ROOT / "config" / "universities.yml"


def _client():
    import httpx

    return httpx.Client(headers={"User-Agent": "ProFinderPipeline/0.2"}, follow_redirects=True)


def _cached(sub: str, parser, seeds) -> dict:
    out = {}
    for s in seeds:
        slug = norm_en(s.name)
        p = CACHE / sub / f"{slug}.json"
        if p.is_file():
            out[slug] = parser(json.loads(p.read_text(encoding="utf-8")))
    return out


def _moe_path(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    for n in ("moe_universities.xls", "moe_universities.xlsx", "moe_universities.csv"):
        if (INPUTS / n).is_file():
            return INPUTS / n
    raise SystemExit("MOE file not found in inputs/ (expected moe_universities.xls)")


def _campus_paths(args) -> list[Path]:
    if args.campuschina:
        return [Path(p) for p in args.campuschina]
    cands = [INPUTS / "campuschina", INPUTS / "campuschina_universities.json"]
    return [p for p in cands if p.exists()]


def _existing(args) -> list[Existing]:
    if args.skip_db:
        return []
    try:
        if args.existing_csv:
            from .existing import load_existing_csv

            return load_existing_csv(args.existing_csv)
        from .existing import load_existing_db

        return load_existing_db()
    except Exception as e:
        # only our own RuntimeError text is shown: driver errors can echo connection strings
        why = str(e) if isinstance(e, RuntimeError) else type(e).__name__
        print(f"WARNING: could not read existing University rows ({why}); continuing without them.")
        return []


def _merge(args, seeds, checks):
    moe = load_moe(_moe_path(args.moe))
    campus = load_campuschina(_campus_paths(args))
    print(f"MOE rows: {len(moe)}; CampusChina universities: {len(campus)}")
    return merge_all(seeds, moe, campus, _cached("openalex", oa.parse_response, seeds), _cached("ror", rorm.parse_response, seeds), _existing(args), checks)


def cmd_check_seed(args) -> int:
    seeds = load_seed(SEED)
    names = [s.name for s in seeds]
    ok = len(names) == len(set(names)) == 104
    print(f"seed entries: {len(names)} (unique {len(set(names))}) -> {'OK' if ok else 'FAIL'}")
    t = INPUTS / "ingest-targets.json"
    if t.is_file():
        want = json.loads(t.read_text(encoding="utf-8"))
        diffs = [(w, n) for w, n in zip(want, names) if norm_en(w) != norm_en(n)]
        same = len(want) == len(names) and not diffs
        print(f"same universities in the same order as inputs/ingest-targets.json (spacing/case ignored): {same}")
        for w, n in diffs:
            print(f"  differs: targets file {w!r} vs seed {n!r}")
        spacing_only = [(w, n) for w, n in zip(want, names) if w != n and norm_en(w) == norm_en(n)]
        if spacing_only:
            print(f"  note: {len(spacing_only)} name(s) differ only in spacing/case (harmless, matching ignores it): {spacing_only}")
        ok = ok and same
    moe = load_moe(_moe_path(args.moe))
    have = {r.name_zh for r in moe}
    miss = [s.name for s in seeds if s.name_zh not in have]
    print(f"nameZh found in MOE list: {len(seeds) - len(miss)}/{len(seeds)}" + (f"; missing: {miss}" if miss else ""))
    return 0 if ok and not miss else 1


def cmd_probe(args) -> int:
    load_env()
    c = _client()
    name = "Tsinghua University"
    p1 = oa.fetch(name, norm_en(name), CACHE, os.environ.get("OPENALEX_API_KEY"), c, force=True)
    d = json.loads(p1.read_text(encoding="utf-8"))
    print("OpenAlex: top-level keys:", sorted(d.keys()), "| results:", len(d.get("results", [])))
    for r in oa.parse_response(d)[:3]:
        print("  ", r)
    p2 = rorm.fetch(name, norm_en(name), CACHE, os.environ.get("ROR_CLIENT_ID"), c, force=True)
    d = json.loads(p2.read_text(encoding="utf-8"))
    print("ROR: top-level keys:", sorted(d.keys()), "| items:", len(d.get("items", [])))
    for r in rorm.parse_response(d)[:3]:
        print("  ", r)
    print(f"raw responses cached in {CACHE}")
    return 0


def _fetch_all(args, which: str) -> int:
    load_env()
    c = _client()
    seeds = load_seed(SEED)[: args.limit or None]
    fail = 0
    for i, s in enumerate(seeds, 1):
        slug = norm_en(s.name)
        try:
            if which == "openalex":
                path = oa.cache_path(CACHE, slug)
                fresh = not path.exists() or args.force
                oa.fetch(s.name, slug, CACHE, os.environ.get("OPENALEX_API_KEY"), c, force=args.force)
                pause = 0.3
            else:
                path = rorm.cache_path(CACHE, slug)
                fresh = not path.exists() or args.force
                cid = os.environ.get("ROR_CLIENT_ID")
                rorm.fetch(s.name, slug, CACHE, cid, c, force=args.force)
                pause = 0.6 if cid else args.ror_delay
            print(f"[{i}/{len(seeds)}] {s.name}: {'fetched' if fresh else 'cached'}")
            if fresh:
                time.sleep(pause)
        except Exception as e:
            fail += 1
            print(f"[{i}/{len(seeds)}] {s.name}: FAILED {e}")
    print(f"done, failures: {fail}")
    return 1 if fail else 0


def cmd_check_urls(args) -> int:
    seeds = load_seed(SEED)
    merged = _merge(args, seeds, load_checks(CACHE / "url_checks.json"))
    urls = sorted({m.official_url for m in merged if m.official_url})
    print(f"checking {len(urls)} official URLs (cached ones are skipped)")
    checks = check_urls(urls, CACHE / "url_checks.json", _client(), delay=args.delay, force=args.force, retry_failed=args.retry_failed)
    ok = sum(1 for u in urls if checks.get(u, {}).get("status") == 200)
    print(f"HTTP 200: {ok}/{len(urls)}")
    return 0


def cmd_diagnose(args) -> int:
    seeds = load_seed(SEED)
    checks = load_checks(CACHE / "url_checks.json")
    print(diagnose(_merge(args, seeds, checks), checks))
    return 0


def cmd_build(args) -> int:
    seeds = load_seed(SEED)
    merged = _merge(args, seeds, load_checks(CACHE / "url_checks.json"))
    ts = PIPELINE_ROOT / "output" / "discovery" / "targets_summary.json"
    targets = json.loads(ts.read_text(encoding="utf-8")) if ts.is_file() else None
    csv_p, json_p = write_report(merged, OUT, targets)
    doms = build_domains(merged, seeds)
    dom_p = PIPELINE_ROOT / "config" / "domains.yml" if args.write_domains else OUT / "domains.yml"
    dump_domains(doms, dom_p)
    print(summary(merged))
    no_dom = [n for n, d in doms.items() if not d["domains"]]
    print(f"universities with >= 1 allowed domain: {len(doms) - len(no_dom)}/{len(doms)}" + (f" (none: {len(no_dom)})" if no_dom else ""))
    print(f"wrote {csv_p.relative_to(PIPELINE_ROOT)}, {json_p.relative_to(PIPELINE_ROOT)}, {dom_p.relative_to(PIPELINE_ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m profinder_pipeline.registry", description="Registry stage (tasks 2.1-2.4, 2.6)")
    ap.add_argument("--moe")
    ap.add_argument("--campuschina", nargs="*")
    ap.add_argument("--existing-csv")
    ap.add_argument("--skip-db", action="store_true", help="do not read the University table")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-seed", help="validate config/universities.yml against inputs")
    sub.add_parser("probe", help="call ROR and OpenAlex once and show the response shape")
    for n in ("fetch-openalex", "fetch-ror"):
        p = sub.add_parser(n)
        p.add_argument("--limit", type=int)
        p.add_argument("--force", action="store_true")
        p.add_argument("--ror-delay", type=float, default=6.5)
    p = sub.add_parser("check-urls")
    p.add_argument("--delay", type=float, default=1.5)
    p.add_argument("--force", action="store_true")
    p.add_argument("--retry-failed", action="store_true", help="re-check only URLs that did not return 200")
    sub.add_parser("diagnose", help="list every university that is not clean, with flags and URL status")
    p = sub.add_parser("build")
    p.add_argument("--write-domains", action="store_true", help="write config/domains.yml (default: output/registry/domains.yml)")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):   # Windows redirects default to cp1252, which cannot print Chinese
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_env()   # DB URL and API keys from pipeline/.env (values are never printed)
    return {
        "check-seed": cmd_check_seed, "probe": cmd_probe, "check-urls": cmd_check_urls, "build": cmd_build, "diagnose": cmd_diagnose,
        "fetch-openalex": lambda x: _fetch_all(x, "openalex"), "fetch-ror": lambda x: _fetch_all(x, "ror"),
    }[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
