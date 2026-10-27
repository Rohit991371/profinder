"""Task 6.3 (fetch side): adaptive delay and breaker cool-down. Defaults keep the old behaviour (existing gate tests stay valid)."""
from fetch_helpers import FakeResponse, make_engine, ok_page
import httpx

from profinder_pipeline.fetch.gate import Gate, RetryPolicy, Throttle
from profinder_pipeline.fetch.static import StaticFetcher


def throttle(t, **kw):
    slept = []
    th = Throttle(2.5, 0.0, lambda: t[0], lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)), lambda: 0.0, **kw)
    return th, slept


def test_slow_answers_double_the_delay_and_fast_ones_halve_it():
    t = [100.0]
    th, slept = throttle(t, slow_after=8.0, max_factor=8.0)
    h = "a.edu.cn"
    th.wait(h)
    th.observe(h, 20.0)                      # slow
    assert th.factor(h) == 2.0
    assert th.wait(h) == 5.0                 # 2.5 x 2
    th.observe(h, 30.0); th.observe(h, 30.0); th.observe(h, 30.0)
    assert th.factor(h) == 8.0               # capped
    th.observe(h, 1.0)
    assert th.factor(h) == 4.0 and th.factor("other.edu.cn") == 1.0
    th.observe(h, 1.0); th.observe(h, 1.0); th.observe(h, 1.0)
    assert th.factor(h) == 1.0               # never below 1x: politeness is never reduced
    th.observe(h, 5.0)                       # between slow/2 and slow: unchanged
    assert th.factor(h) == 1.0
    th.observe(h, 0.1, ok=False)             # a failure counts as slow
    assert th.factor(h) == 2.0


def test_no_slow_after_means_the_old_behaviour():
    t = [0.0]
    th, _ = throttle(t)
    th.observe("a.edu.cn", 99.0, ok=False)
    assert th.factor("a.edu.cn") == 1.0


class RecThrottle:
    def __init__(self):
        self.seen = []

    def wait(self, host):
        return 0.0

    def observe(self, host, seconds, ok=True):
        self.seen.append((host, ok))


def test_static_fetcher_reports_each_answer_to_the_throttle():
    from fetch_helpers import FakeClient
    th = RecThrottle()
    client = FakeClient({"https://a.edu.cn/x": [FakeResponse(503, "x"), ok_page()], "https://a.edu.cn/y": httpx.ConnectTimeout("t")})
    f = StaticFetcher(client, th, RetryPolicy(3, 0.0, 0.0), lambda s: None)
    assert f.get("https://a.edu.cn/x").kind == "ok"
    assert th.seen[:2] == [("a.edu.cn", False), ("a.edu.cn", True)]          # 503 = not ok, then 200
    th.seen.clear()
    f.get("https://a.edu.cn/y")
    assert th.seen and all(not ok for _, ok in th.seen)


def gate(clock, cooldown=100.0):
    g = Gate(lambda base: ("none", None), cooldown=cooldown, clock=lambda: clock[0])
    g.begin(["example.edu.cn"], 100)
    return g


def trip(g, n=3):
    for _ in range(n):
        g.note_failure("example.edu.cn")


def test_breaker_cooldown_half_open_and_doubling():
    c = [0.0]
    g = gate(c)
    u = "https://example.edu.cn/a"
    trip(g)
    assert g.check(u).reason == "host_blocked" and g.cooldown_remaining("example.edu.cn") == 100.0
    c[0] = 99.0
    assert g.check(u).reason == "host_blocked"
    c[0] = 101.0
    assert g.check(u).allowed                                    # half-open: one probe
    g.note_failure("example.edu.cn")                             # the probe fails: tripped again, twice as long
    assert g.check(u).reason == "host_blocked" and g.cooldown_remaining("example.edu.cn") == 200.0
    c[0] = 400.0
    assert g.check(u).allowed
    g.note_success("example.edu.cn")
    trip(g, 2)
    assert g.check(u).allowed                                    # success reset the failure count


def test_without_cooldown_a_tripped_host_stays_blocked_until_the_next_university():
    c = [0.0]
    g = gate(c, cooldown=None)
    trip(g)
    c[0] = 10 ** 6
    assert g.check("https://example.edu.cn/a").reason == "host_blocked" and g.cooldown_remaining("example.edu.cn") == 0.0
    g.begin(["example.edu.cn"], 5)
    assert g.check("https://example.edu.cn/a").allowed


def test_waiting_budget():
    g = gate([0.0])
    assert g.may_wait(100.0)
    g.note_waited(850.0)
    assert not g.may_wait(100.0) and g.may_wait(50.0)
    assert not Gate(lambda b: ("none", None)).may_wait(1.0)      # no cooldown configured: never waits


def test_engine_waits_through_the_cooldown_then_probes_once(tmp_path):
    urls = [f"https://www.example.edu.cn/f{i}.htm" for i in range(5)]
    routes = {urls[0]: httpx.ConnectError("c"), urls[1]: httpx.ConnectError("c"), urls[2]: httpx.ConnectError("c"), urls[3]: ok_page(), urls[4]: ok_page()}
    e = make_engine(tmp_path, routes)
    clock = e.clock
    e.gate.cooldown, e.gate._clock = 60.0, clock.now
    slept = []
    e._sleep = lambda s: (slept.append(s), clock.sleep(s))
    for u in urls[:3]:
        assert e.get(u).error == "connect_error"
    p = e.get(urls[3])                                           # tripped: the engine sleeps through the cooldown, then probes
    assert p.ok and any(s >= 60 for s in slept)
    assert e.get(urls[4]).ok                                     # and the host is healthy again
    e2 = make_engine(tmp_path / "b", routes)
    for u in urls[:3]:
        e2.get(u)
    assert e2.get(urls[3]).error == "host_blocked"               # no cooldown configured: the old behaviour


def test_fetch_cached_shows_what_was_saved(tmp_path, monkeypatch, capsys):
    """`fetch cached URL` is the first thing to run when a site config 'does not work': it shows whether the saved page is real content."""
    import argparse
    from profinder_pipeline.fetch import run as FR
    from profinder_pipeline.fetch.cache import PageCache
    import profinder_pipeline.paths as P
    monkeypatch.setattr(P, "INPUTS_DIR", tmp_path)
    cache = PageCache(tmp_path / "cache" / "pages")
    url = "https://faculty.example.edu.cn/guolin/zh_CN/index.htm"
    cache.write(url, url, "<html><body><h1>郭霖</h1><p>mail: a@b.edu.cn</p><script>x()</script></body></html>", 200, "static", "utf-8", "h")
    assert FR.cmd_cached(argparse.Namespace(url=url, name="郭霖", chars=200, around=None, context=50)) == 0
    out = capsys.readouterr().out
    assert "status 200" in out and "in visible text: True" in out and "HINT: very little visible text" in out
    assert FR.cmd_cached(argparse.Namespace(url="https://faculty.example.edu.cn/none", name=None, chars=10, around=None, context=50)) == 1
    assert FR.cmd_cached(argparse.Namespace(url=url, name=None, chars=10, around=["<h1>", "absent text"], context=20)) == 0
    out2 = capsys.readouterr().out
    assert "raw HTML around '<h1>'" in out2 and "(not found)" in out2 and "<body><h1>郭霖</h1>" in out2
