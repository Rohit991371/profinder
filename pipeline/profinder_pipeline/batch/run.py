"""`python -m profinder_pipeline batch ...` (task 6.3): run the existing stages for a rotating batch of universities.

  batch run [--n 5] [--only NAME ...] [--stages fetch,extract,match] [--budget 200] [--rounds 3] [--llm --llm-limit 40]
            [--lock-policy strict|import-ok] [--retry-after-days 7] [--time-limit-min N] [--learn] [--dry-run]
  batch status          the state table
  batch report          rewrite output/batch/batch_report.{csv,md} from the state file

Each university runs: fetch (several budgeted rounds until all planned profile pages are cached) -> extract -> match. Nothing here writes to the
app database and nothing loads: the batch only fills ingest.* and prepares candidates (a person builds and reviews the release).
Resumable: state is saved after every stage (output/batch/state.json); Ctrl-C marks the current university `interrupted`, and the next run
starts with it. A bot-blocked or robots-blocked university is NOT retried with tricks: it ends as `blocked_needs_human_pages` (supply saved pages,
use another network or a proxy). A university without a reviewed config/sites/<slug>.yml ends as `needs_site_config` (or `proposal_ready` with --learn)."""
from __future__ import annotations

import argparse
import contextlib
import csv
import io
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from ..paths import CONFIG_DIR, OUTPUT_DIR
from . import state as S

OUT = OUTPUT_DIR / "batch"
STATE = OUT / "state.json"
LLM_LOG = OUTPUT_DIR / "extract" / "llm_calls.jsonl"
PROPOSED = CONFIG_DIR / "sites" / "_proposed"


class Tee(io.TextIOBase):
    """Show the stage's output as it happens AND keep it (for the note and for diagnosing a failure)."""

    def __init__(self, real) -> None:
        self.real, self.buf = real, io.StringIO()

    def write(self, s: str) -> int:
        self.buf.write(s)
        try:
            self.real.write(s)
        except Exception:  # noqa: BLE001 - a console that cannot print must not stop a long run
            pass
        return len(s)

    def flush(self) -> None:
        try:
            self.real.flush()
        except Exception:  # noqa: BLE001
            pass

    def tail(self, n: int = 3) -> str:
        return " | ".join(x.strip() for x in self.buf.getvalue().strip().splitlines()[-n:])[:300]


def call_stage(fn: Callable[[list], int], argv: list) -> tuple:
    """-> (exit_code, seconds, tail). A SystemExit('message') from a stage is a failure with that message, not a crash of the batch."""
    t0, tee, code = time.monotonic(), Tee(sys.stdout), 0
    with contextlib.redirect_stdout(tee):
        try:
            code = int(fn(argv) or 0)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
            if not isinstance(exc.code, int) and exc.code:
                print(str(exc.code))
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001
            code = 1
            print(f"{type(exc).__name__}: {exc}")
    return code, time.monotonic() - t0, tee.tail()


def _stage_fns() -> dict:
    from ..extract.run import main as extract_main
    from ..fetch.run import main as fetch_main
    from ..match.run import main as match_main
    return {"fetch": fetch_main, "extract": extract_main, "match": match_main}


def _names() -> list:
    import yaml
    data = yaml.safe_load((CONFIG_DIR / "universities.yml").read_text(encoding="utf-8")) or {}
    return [u["name"] for u in data.get("universities", [])]


def _q(conn, sql: str, params: tuple = ()) -> list:
    return conn.execute(sql, params).fetchall()


def list_state(conn, name: str) -> tuple:
    """-> (list problem or None, profile targets still pending, list url)"""
    rows = _q(conn, "SELECT status, last_error, url FROM ingest.target WHERE university_name = %s AND kind = 'faculty_list' "
                    "ORDER BY (status = 'ok') DESC, last_fetch_at DESC NULLS LAST LIMIT 1", (name,))
    problem = S.classify_list_target(rows[0][0], rows[0][1]) if rows else "no_faculty_list_found"
    pending = _q(conn, "SELECT count(*) FROM ingest.target WHERE university_name = %s AND kind = 'faculty_profile' AND status = 'pending'", (name,))[0][0]
    return problem, int(pending), (rows[0][2] if rows else None)


def page_stats(conn, name: str, since: datetime) -> dict:
    out = {"static": 0, "playwright": 0, "errors": 0}
    for mode, err, n in _q(conn, "SELECT p.render_mode, (p.error IS NOT NULL), count(*) FROM ingest.page p JOIN ingest.target t USING (target_id) "
                                 "WHERE t.university_name = %s AND p.fetched_at >= %s GROUP BY 1, 2", (name, since)):
        if err:
            out["errors"] += int(n)
        elif mode in out:
            out[mode] += int(n)
    return out


def candidate_stats(conn, name: str) -> dict:
    return {d: int(n) for d, n in _q(conn, "SELECT decision, count(*) FROM ingest.current_candidate WHERE university_name = %s AND entity_type = 'professor' "
                                           "GROUP BY 1", (name,))}


def run_university(conn, name: str, args, rec: dict, save: Callable[[dict], None]) -> dict:
    fns = _stage_fns()
    since = S.now_utc()
    from ..extract.sitecfg import load_site_configs
    has_config = name in load_site_configs()
    rec.update({"has_site_config": has_config, "status": "interrupted", "last_run": S.stamp(since), "_count_attempt": True, "note": "", "stages": {},
                "pages": {}, "candidates": {}, "groq": {}})
    save(rec)
    rec.pop("_count_attempt")
    stages = set(args.stages.split(","))
    problem, pending, list_url = list_state(conn, name)
    if "fetch" in stages:
        for rnd in range(1, args.rounds + 1):
            code, secs, tail = call_stage(fns["fetch"], ["run", "--only", name, "--budget", str(args.budget)])
            old = (rec.get("stages") or {}).get("fetch") or {}
            rec.setdefault("stages", {})["fetch"] = {"seconds": float(old.get("seconds") or 0) + secs if rnd > 1 else secs, "code": code, "rounds": rnd, "tail": tail}
            problem, pending, list_url = list_state(conn, name)
            save(rec)
            if code != 0 or problem or pending == 0:
                break
    if (problem and problem.startswith("blocked")) or problem == "no_faculty_list_found":
        rec.update({"note": problem, "profiles_pending": pending})
        stages -= {"extract", "match"}
    if not has_config and "extract" in stages and not (problem and problem.startswith(("blocked", "failed"))):
        stages -= {"extract", "match"}
        if args.learn and list_url:
            code, secs, tail = call_stage(_stage_fns()["extract"], ["site-learn", "--university", name, "--list-url", list_url] + ([] if args.learn_llm else ["--no-llm"]))
            rec.setdefault("stages", {})["learn"] = {"seconds": secs, "code": code, "tail": tail}
            fresh = [p for p in PROPOSED.glob("*.yml") if p.stat().st_mtime >= since.timestamp()] if PROPOSED.is_dir() else []
            rec["note"] = ("proposal in config/sites/_proposed/ (review it, set needs_review: false, move it up one folder)" if fresh else "site-learn found no proposal")
            rec["_proposal"] = bool(fresh)
        else:
            rec["note"] = "no reviewed config/sites/<slug>.yml: run `extract site-learn` (or batch --learn) and review the proposal"
    extract_ok = match_ok = None
    if "extract" in stages:
        argv = ["run", "--only", name] + (["--llm", "--llm-limit", str(args.llm_limit)] if args.llm else [])
        code, secs, tail = call_stage(fns["extract"], argv)
        rec.setdefault("stages", {})["extract"] = {"seconds": secs, "code": code, "tail": tail}
        extract_ok = code == 0
        save(rec)
    if "match" in stages and extract_ok is not False:
        code, secs, tail = call_stage(fns["match"], ["run", "--only", name, "--lock-policy", args.lock_policy])
        rec.setdefault("stages", {})["match"] = {"seconds": secs, "code": code, "tail": tail}
        match_ok = code == 0
    status = S.decide_status(has_config, problem, pending, extract_ok, match_ok)
    if status == "needs_site_config" and rec.get("_proposal"):
        status = "proposal_ready"
    if status == "failed" and not rec.get("note"):
        rec["note"] = next((v.get("tail") for v in (rec.get("stages") or {}).values() if v.get("code")), "") or (problem or "")
    rec.pop("_proposal", None)
    rec.update({"status": status, "profiles_pending": pending, "pages": page_stats(conn, name, since), "candidates": candidate_stats(conn, name),
                "groq": S.sum_llm_log(LLM_LOG, since)})
    if status == "partial":
        rec["note"] = f"{pending} profile pages not fetched yet: run again"
    return rec


def write_reports(state: dict) -> None:
    rows = [S.report_row(n, r) for n, r in sorted(state["universities"].items())]
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "batch_report.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=S.REPORT_COLS)
        w.writeheader()
        w.writerows(rows)
    (OUT / "batch_report.md").write_text(S.report_md(rows), encoding="utf-8")


def cmd_run(args) -> int:
    names = _names()
    state = S.load_state(STATE)
    todo = S.pick(names, state, args.n, S.now_utc(), args.retry_after_days, args.only)
    if args.only:
        missing = [o for o in args.only if o not in names]
        if missing:
            print("not in config/universities.yml:", missing)
            return 1
    from ..extract.sitecfg import load_site_configs
    cfg = load_site_configs()
    print(f"{len(todo)} universities this run (of {len(names)}): " + "; ".join(f"{n}{'' if n in cfg else ' [no site config]'}" for n in todo))
    if args.dry_run or not todo:
        return 0
    from ..release.run import connect_ingest
    conn = connect_ingest()
    t_start, done = time.monotonic(), 0
    try:
        for name in todo:
            if args.time_limit_min and (time.monotonic() - t_start) / 60 >= args.time_limit_min:
                print(f"time limit {args.time_limit_min} min reached: stopping before {name}")
                break
            print(f"\n=== [{done + 1}/{len(todo)}] {name}")
            rec = dict(state["universities"].get(name) or {})

            def save(r: dict, _n: str = name) -> None:
                old = state["universities"].get(_n)
                state["universities"][_n] = S.merge_record(old, r)
                S.save_state(STATE, state)

            try:
                rec = run_university(conn, name, args, rec, save)
            except KeyboardInterrupt:
                state["universities"].setdefault(name, {})["status"] = "interrupted"
                S.save_state(STATE, state)
                print(f"\ninterrupted during {name}: the next batch run starts with it")
                write_reports(state)
                return 130
            except Exception as exc:  # noqa: BLE001 - one university must not stop the batch
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                rec.update({"status": "failed", "note": f"{type(exc).__name__}: {exc}"[:300]})
            state["universities"][name] = S.merge_record(state["universities"].get(name), rec)
            S.save_state(STATE, state)
            done += 1
            print(f"=== {name}: {rec['status']} {rec.get('note') or ''}")
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    write_reports(state)
    print(f"\nreport: {OUT / 'batch_report.md'} and batch_report.csv")
    return 0


def cmd_status(args) -> int:
    state = S.load_state(STATE)
    names = _names()
    for n in names:
        r = state["universities"].get(n)
        print(f"{n:55s} {r['status'] if r else '-':26s} {(r or {}).get('last_run', '')}")
    return 0


def cmd_report(args) -> int:
    write_reports(S.load_state(STATE))
    print(f"written {OUT / 'batch_report.md'}")
    return 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="profinder_pipeline batch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--n", type=int, default=5, help="universities per run (default 5)")
    r.add_argument("--only", action="append", default=[], help="university name (repeatable); overrides the rotation")
    r.add_argument("--stages", default="fetch,extract,match")
    r.add_argument("--budget", type=int, default=200, help="network fetches per university per fetch round (default 200)")
    r.add_argument("--rounds", type=int, default=3, help="fetch rounds per university until all profile pages are cached (default 3)")
    r.add_argument("--llm", action="store_true", help="Groq interests where a page has no interests section (needs --llm-limit)")
    r.add_argument("--llm-limit", type=int, default=40)
    r.add_argument("--lock-policy", choices=("strict", "import-ok"), default="strict")
    r.add_argument("--retry-after-days", type=float, default=7.0)
    r.add_argument("--time-limit-min", type=float, help="do not start a new university after N minutes")
    r.add_argument("--learn", action="store_true", help="for a university without a site config, run site-learn (heuristic) and stop there")
    r.add_argument("--learn-llm", action="store_true", help="with --learn: also use one Groq call")
    r.add_argument("--dry-run", action="store_true", help="only print which universities would run")
    sub.add_parser("status")
    sub.add_parser("report")
    a = ap.parse_args(argv)
    return {"run": cmd_run, "status": cmd_status, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
