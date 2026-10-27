"""Batch runner core (task 6.3): rotation, resumable state, classification of a university's outcome, cost report. Pure Python (no database,
no network): the stages themselves are the existing commands, called by batch/run.py.

State file: output/batch/state.json  {"universities": {name: {status, attempts, last_run, stages: {...}, note}}}
Statuses: done | partial (budget stopped the profile fetch) | blocked_needs_human_pages | needs_site_config | proposal_ready | failed | interrupted"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

STATUSES = ("done", "partial", "blocked_needs_human_pages", "needs_site_config", "proposal_ready", "failed", "interrupted")
# a status that is picked again at once (a person has fixed something, or it was cut short); others wait `retry_after_days`
RESUME_FIRST = ("interrupted", "partial")
BLOCK_MARKERS = ("http_412", "http_403", "render_http_412", "render_http_403", "host_blocked", "robots_disallowed", "robots_unreadable",
                 "needs_renderer", "challenge_page", "timeout", "connect_error")


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            d.setdefault("universities", {})
            return d
        except ValueError:
            pass
    return {"universities": {}}


def save_state(path: Path, state: dict) -> None:
    """Atomic: a crash while saving never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _when(s: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def pick(names: list, state: dict, n: int, now: datetime, retry_after_days: float = 7.0, only: Optional[list] = None) -> list:
    """Which universities to run now. Order: interrupted/partial first, then never-run (in the given order), then the oldest finished.
    A finished one is not picked again before `retry_after_days`; `only` overrides rotation (still limited to n)."""
    if only:
        return [x for x in names if x in set(only)][:n] if n else [x for x in names if x in set(only)]
    uni = state.get("universities", {})
    first, fresh, older = [], [], []
    for name in names:
        s = uni.get(name)
        if s is None:
            fresh.append(name)
            continue
        last = _when(s.get("last_run"))
        if s.get("status") in RESUME_FIRST:
            first.append((last or datetime.min.replace(tzinfo=timezone.utc), name))
        elif last is None or now - last >= timedelta(days=retry_after_days):
            older.append((last or datetime.min.replace(tzinfo=timezone.utc), name))
    order = [x for _, x in sorted(first)] + fresh + [x for _, x in sorted(older)]
    return order[:n]


def classify_list_target(status: Optional[str], last_error: Optional[str]) -> Optional[str]:
    """None = the list was read. Otherwise a short reason: why a person has to supply pages, or why there is nothing to read."""
    if status in ("ok",):
        return None
    err = (last_error or "").lower()
    if status == "no_page_found":
        return "no_faculty_list_found"
    if status == "blocked" or any(m in err for m in BLOCK_MARKERS):
        return f"blocked:{(last_error or status or '?')[:60]}"
    if status == "failed":
        return f"failed:{(last_error or '?')[:60]}"
    return None if status is None else f"list_not_fetched:{status}"


def decide_status(has_config: bool, list_problem: Optional[str], pending_profiles: int, extract_ok: Optional[bool], match_ok: Optional[bool]) -> str:
    if list_problem and list_problem.startswith("blocked"):
        return "blocked_needs_human_pages"
    if list_problem == "no_faculty_list_found":
        return "done"                       # nothing to extract is a legitimate end state (no computing school)
    if list_problem:
        return "failed"
    if not has_config:
        return "needs_site_config"
    if extract_ok is False or match_ok is False:
        return "failed"
    return "partial" if pending_profiles > 0 else "done"


def sum_llm_log(path: Path, since: datetime) -> dict:
    """Groq usage from the JSONL token log, only calls since `since` (UTC). Missing log = zeros."""
    out = {"calls": 0, "prompt": 0, "completion": 0, "usd": 0.0, "errors": 0}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        t = _when(r.get("ts"))
        if t is None or t < since:
            continue
        if r.get("status") == "ok":
            out["calls"] += 1
            out["prompt"] += int(r.get("prompt_tokens") or 0)
            out["completion"] += int(r.get("completion_tokens") or 0)
            out["usd"] += float(r.get("est_usd") or 0.0)
        else:
            out["errors"] += 1
    out["usd"] = round(out["usd"], 5)
    return out


REPORT_COLS = ["university", "status", "note", "has_site_config", "attempts", "fetch_s", "extract_s", "match_s", "total_s", "pages_static", "pages_rendered",
               "page_errors", "profiles_pending", "candidates_auto_accept", "candidates_needs_review", "candidates_rejected", "groq_calls", "groq_tokens", "groq_usd"]


def report_row(name: str, rec: dict) -> dict:
    st = rec.get("stages", {})
    sec = lambda k: round(float((st.get(k) or {}).get("seconds") or 0.0), 1)   # noqa: E731
    c = rec.get("candidates") or {}
    g = rec.get("groq") or {}
    pg = rec.get("pages") or {}
    return {"university": name, "status": rec.get("status"), "note": rec.get("note") or "", "has_site_config": rec.get("has_site_config"),
            "attempts": rec.get("attempts"), "fetch_s": sec("fetch"), "extract_s": sec("extract"), "match_s": sec("match"),
            "total_s": round(sum(sec(k) for k in ("learn", "fetch", "extract", "programs", "match")), 1), "pages_static": pg.get("static", 0),
            "pages_rendered": pg.get("playwright", 0), "page_errors": pg.get("errors", 0), "profiles_pending": rec.get("profiles_pending"),
            "candidates_auto_accept": c.get("auto_accept", 0), "candidates_needs_review": c.get("needs_review", 0),
            "candidates_rejected": (c.get("rejected", 0) + c.get("human_rejected", 0)), "groq_calls": g.get("calls", 0),
            "groq_tokens": g.get("prompt", 0) + g.get("completion", 0), "groq_usd": g.get("usd", 0.0)}


def report_md(rows: list, extrapolate_to: int = 104) -> str:
    done = [r for r in rows if r["status"] in ("done", "partial") and r["total_s"] > 0]
    tot_s = sum(r["total_s"] for r in rows)
    lines = ["# Batch report", "", f"Universities in this report: {len(rows)} · wall time of their stages: {round(tot_s / 60, 1)} min · "
             f"Groq calls {sum(r['groq_calls'] for r in rows)} · tokens {sum(r['groq_tokens'] for r in rows)} · est. USD {round(sum(r['groq_usd'] for r in rows), 4)}", "",
             "| university | status | fetch s | extract s | match s | static | rendered | errors | profiles pending | auto | review | rejected | groq tokens | note |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['university']} | {r['status']} | {r['fetch_s']} | {r['extract_s']} | {r['match_s']} | {r['pages_static']} | {r['pages_rendered']} | "
                     f"{r['page_errors']} | {r['profiles_pending']} | {r['candidates_auto_accept']} | {r['candidates_needs_review']} | {r['candidates_rejected']} | "
                     f"{r['groq_tokens']} | {r['note']} |")
    if done:
        avg = sum(r["total_s"] for r in done) / len(done)
        lines += ["", f"Average wall time per finished university: {round(avg / 60, 1)} min (n={len(done)}). "
                  f"Rough planning figure for {extrapolate_to} universities: {round(avg * extrapolate_to / 3600, 1)} h, "
                  "only for universities that have a reviewed site config; the rest need `site-learn` and a person first."]
    by: dict[str, int] = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
    lines += ["", "Statuses: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())), ""]
    return "\n".join(lines)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def stamp(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def merge_record(old: Optional[dict], new: dict) -> dict:
    """New values win; `stages` is replaced when given (a record carries all stages of ITS run); attempts count only when asked."""
    rec = dict(old or {})
    rec.update({k: v for k, v in new.items() if k not in ("_count_attempt", "attempts")})
    rec["attempts"] = int((old or {}).get("attempts") or 0) + (1 if new.get("_count_attempt") else 0)
    return rec


def as_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True, default=str)
