"""Task 6.3: batch runner core (rotation, resume, outcome classification, report) and the stage loop with fake stages and a fake database."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from profinder_pipeline.batch import run as B
from profinder_pipeline.batch import state as S

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
NAMES = ["A Univ", "B Univ", "C Univ", "D Univ", "E Univ"]


def iso(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


def test_rotation_order_resume_first_then_never_run_then_oldest():
    st = {"universities": {"A Univ": {"status": "done", "last_run": iso(1)}, "B Univ": {"status": "interrupted", "last_run": iso(2)},
                           "C Univ": {"status": "done", "last_run": iso(30)}, "D Univ": {"status": "partial", "last_run": iso(5)}}}
    assert S.pick(NAMES, st, 10, NOW) == ["D Univ", "B Univ", "E Univ", "C Univ"]   # partial (oldest run first by date), fresh, then the long-finished; A is too recent
    assert S.pick(NAMES, st, 2, NOW) == ["D Univ", "B Univ"]
    assert S.pick(NAMES, st, 3, NOW, only=["C Univ", "A Univ"]) == ["A Univ", "C Univ"]
    assert S.pick(NAMES, {"universities": {}}, 3, NOW) == NAMES[:3]


def test_state_file_roundtrip_is_atomic(tmp_path):
    p = tmp_path / "x" / "state.json"
    assert S.load_state(p) == {"universities": {}}
    S.save_state(p, {"universities": {"A": {"status": "done"}}})
    assert S.load_state(p)["universities"]["A"]["status"] == "done" and not list(p.parent.glob("*.tmp"))
    p.write_text("{broken", encoding="utf-8")
    assert S.load_state(p) == {"universities": {}}


def test_list_problem_and_status_decisions():
    assert S.classify_list_target("ok", None) is None
    assert S.classify_list_target("no_page_found", None) == "no_faculty_list_found"
    assert S.classify_list_target("failed", "render_http_412").startswith("blocked")
    assert S.classify_list_target("blocked", "robots_disallowed").startswith("blocked")
    assert S.classify_list_target("failed", "not_a_faculty_list (3 person links)").startswith("failed")
    assert S.decide_status(True, "blocked:http_412", 5, None, None) == "blocked_needs_human_pages"
    assert S.decide_status(False, "no_faculty_list_found", 0, None, None) == "done"
    assert S.decide_status(False, None, 0, None, None) == "needs_site_config"
    assert S.decide_status(True, None, 12, True, True) == "partial" and S.decide_status(True, None, 0, True, True) == "done"
    assert S.decide_status(True, None, 0, False, None) == "failed" and S.decide_status(True, "failed:x", 0, None, None) == "failed"


def test_groq_usage_only_counts_calls_since_the_start(tmp_path):
    log = tmp_path / "l.jsonl"
    rows = [{"ts": iso(2), "status": "ok", "prompt_tokens": 1000, "completion_tokens": 10, "est_usd": 0.1},
            {"ts": (NOW + timedelta(minutes=1)).isoformat(), "status": "ok", "prompt_tokens": 300, "completion_tokens": 20, "est_usd": 0.002},
            {"ts": (NOW + timedelta(minutes=2)).isoformat(), "status": "http_429", "prompt_tokens": 0, "completion_tokens": 0, "est_usd": 0}]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    g = S.sum_llm_log(log, NOW)
    assert g == {"calls": 1, "prompt": 300, "completion": 20, "usd": 0.002, "errors": 1}
    assert S.sum_llm_log(tmp_path / "missing", NOW)["calls"] == 0


def test_merge_record_counts_attempts_only_when_asked_and_replaces_stages():
    a = S.merge_record(None, {"status": "interrupted", "_count_attempt": True, "stages": {"fetch": {"seconds": 1}}})
    assert a["attempts"] == 1 and a["stages"] == {"fetch": {"seconds": 1}}
    b = S.merge_record(a, {"status": "done", "stages": {"extract": {"seconds": 2}}})
    assert b["attempts"] == 1 and b["stages"] == {"extract": {"seconds": 2}} and b["status"] == "done"
    assert S.merge_record(b, {"_count_attempt": True})["attempts"] == 2


def test_report_rows_and_markdown():
    rec = {"status": "done", "stages": {"fetch": {"seconds": 120.0}, "extract": {"seconds": 10.0}, "match": {"seconds": 5.0}}, "pages": {"static": 40, "playwright": 2, "errors": 1},
           "candidates": {"auto_accept": 100, "needs_review": 5, "rejected": 3, "human_rejected": 1}, "groq": {"calls": 4, "prompt": 3000, "completion": 100, "usd": 0.01},
           "profiles_pending": 0, "attempts": 1, "has_site_config": True}
    r = S.report_row("A Univ", rec)
    assert r["total_s"] == 135.0 and r["candidates_rejected"] == 4 and r["groq_tokens"] == 3100 and set(r) == set(S.REPORT_COLS)
    blocked = S.report_row("B Univ", {"status": "blocked_needs_human_pages", "note": "blocked:http_412"})
    md = S.report_md([r, blocked])
    assert "| A Univ | done |" in md and "blocked_needs_human_pages 1" in md and "for 104 universities" in md


# ---------------------------------------------------------------------------------------------- stage loop with fakes
class FakeConn:
    """Answers the batch queries from a dict; records nothing else (the batch never writes to the database here)."""

    def __init__(self, list_row, pending, pages=(), cands=()):
        self.list_row, self.pending, self.pages, self.cands = list_row, pending, list(pages), list(cands)

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        assert s.upper().startswith("SELECT"), s
        res = [(self.list_row[0], self.list_row[1], self.list_row[2])] if "kind = 'faculty_list'" in s and self.list_row else \
              [(self.pending,)] if "faculty_profile" in s else self.pages if "ingest.page" in s else self.cands if "current_candidate" in s else []
        return SimpleNamespace(fetchall=lambda: res)

    def rollback(self): pass
    def close(self): pass


def args(**kw):
    base = dict(stages="fetch,extract,match", budget=50, rounds=3, llm=False, llm_limit=10, lock_policy="strict", learn=False, learn_llm=False)
    return SimpleNamespace(**{**base, **kw})


def fakes(calls, codes=None):
    def mk(name):
        def fn(argv):
            calls.append((name, list(argv)))
            c = (codes or {}).get(name, 0)
            if c == "exit":
                raise SystemExit("boom message")
            if c == "raise":
                raise RuntimeError("bad")
            print(f"{name} finished")
            return c
        return fn
    return {k: mk(k) for k in ("fetch", "extract", "match")}


def run_one(monkeypatch, name, conn, a, calls, codes=None):
    monkeypatch.setattr(B, "_stage_fns", lambda: fakes(calls, codes))
    saved = []
    rec = B.run_university(conn, name, a, {}, lambda r: saved.append(dict(r)))
    return rec, saved


def test_full_run_for_a_configured_university(monkeypatch):
    calls = []
    conn = FakeConn(("ok", None, "https://x.edu.cn/list"), 0, pages=[("static", False, 30), ("playwright", False, 1), ("static", True, 2)],
                    cands=[("auto_accept", 100), ("needs_review", 7)])
    rec, saved = run_one(monkeypatch, "Tsinghua University", conn, args(), calls)       # Tsinghua has a reviewed config in config/sites
    assert [c[0] for c in calls] == ["fetch", "extract", "match"]
    assert calls[0][1] == ["run", "--only", "Tsinghua University", "--budget", "50"] and calls[2][1][-2:] == ["--lock-policy", "strict"]
    assert rec["status"] == "done" and rec["pages"] == {"static": 30, "playwright": 1, "errors": 2} and rec["candidates"]["auto_accept"] == 100
    assert saved[0]["status"] == "interrupted" and saved[0].get("_count_attempt")           # saved BEFORE any stage runs: a crash leaves a resumable record
    assert "_count_attempt" not in saved[1]


def test_fetch_rounds_stop_when_all_profile_pages_are_cached(monkeypatch):
    calls = []

    class Conn(FakeConn):
        n = 0

        def execute(self, sql, params=()):
            if "faculty_profile" in " ".join(sql.split()):
                Conn.n += 1
                return SimpleNamespace(fetchall=lambda: [(40 if Conn.n <= 3 else 0,)])
            return super().execute(sql, params)

    conn = Conn(("ok", None, "u"), 0)
    rec, _ = run_one(monkeypatch, "Tsinghua University", conn, args(rounds=5), calls)
    assert [c[0] for c in calls].count("fetch") == 3 and rec["status"] == "done"          # initial check, two rounds with pending, third round done
    calls.clear()
    Conn.n = -100
    rec, _ = run_one(monkeypatch, "Tsinghua University", conn, args(rounds=2), calls)
    assert [c[0] for c in calls].count("fetch") == 2 and rec["status"] == "partial" and "not fetched yet" in rec["note"]


def test_blocked_university_is_not_extracted_and_not_retried_with_tricks(monkeypatch):
    calls = []
    conn = FakeConn(("failed", "render_http_412", "https://www.bupt.edu.cn/"), 0)
    rec, _ = run_one(monkeypatch, "Tsinghua University", conn, args(), calls)
    assert [c[0] for c in calls] == ["fetch"] and rec["status"] == "blocked_needs_human_pages" and rec["note"].startswith("blocked")


def test_no_faculty_list_is_a_legitimate_end_state(monkeypatch):
    calls = []
    rec, _ = run_one(monkeypatch, "Some Law University", FakeConn(None, 0), args(), calls)
    assert rec["status"] == "done" and rec["note"] == "no_faculty_list_found" and [c[0] for c in calls] == ["fetch"]


def test_university_without_a_reviewed_config_is_reported_not_guessed(monkeypatch):
    calls = []
    rec, _ = run_one(monkeypatch, "Unconfigured University", FakeConn(("ok", None, "https://x.edu.cn/list"), 0), args(), calls)
    assert rec["status"] == "needs_site_config" and [c[0] for c in calls] == ["fetch"] and "site-learn" in rec["note"]
    assert calls[0][1][-1] == "--no-profiles"                                               # list only: profile pages wait for a reviewed config
    calls.clear()
    rec, _ = run_one(monkeypatch, "Unconfigured University", FakeConn(("ok", None, "https://x.edu.cn/list"), 0), args(learn=True), calls)
    assert calls[-1][0] == "extract" and calls[-1][1][:5] == ["site-learn", "--university", "Unconfigured University", "--list-url", "https://x.edu.cn/list"]
    assert "--no-llm" in calls[-1][1]                                                       # heuristic only unless --learn-llm
    assert rec["status"] == "needs_site_config" and "no proposal" in rec["note"]


def test_a_failing_stage_is_recorded_not_raised(monkeypatch):
    for code, expect in (("exit", "boom message"), ("raise", "bad"), (1, "")):
        calls = []
        rec, _ = run_one(monkeypatch, "Tsinghua University", FakeConn(("ok", None, "u"), 0), args(stages="fetch,extract"), calls, {"extract": code})
        assert rec["status"] == "failed" and expect in rec["note"], (code, rec)
        assert "match" not in [c[0] for c in calls]                                          # no match after a failed extract


def test_call_stage_shows_and_keeps_the_output(capsys):
    code, secs, tail = B.call_stage(lambda argv: (print("line1"), print("line2"), 0)[-1], [])
    assert code == 0 and secs >= 0 and tail == "line1 | line2" and "line1" in capsys.readouterr().out


def test_programs_stage_runs_between_extract_and_match_only_when_asked(monkeypatch):
    calls = []
    rec, _ = run_one(monkeypatch, "Tsinghua University", FakeConn(("ok", None, "u"), 0), args(programs=True), calls)
    assert [c[0] for c in calls] == ["fetch", "extract", "extract", "match"] and calls[2][1] == ["programs", "--only", "Tsinghua University"]
    assert rec["status"] == "done" and "programs" in rec["stages"]
    calls.clear()
    run_one(monkeypatch, "Tsinghua University", FakeConn(("ok", None, "u"), 0), args(), calls)
    assert [c[0] for c in calls] == ["fetch", "extract", "match"]
