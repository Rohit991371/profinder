import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import httpx
from fetch_helpers import FakeRenderer, FakeResponse, fixture, make_engine, ok_page
from profinder_pipeline.fetch.cache import PageCache
from profinder_pipeline.fetch.hints import Hints, FetchHint
from profinder_pipeline.fetch.render import playwright_proxy

U = "https://www.example.edu.cn/szdw/list.htm"
D = ("example.edu.cn",)


def test_second_run_makes_zero_network_fetches(tmp_path):
    e1 = make_engine(tmp_path, {U: ok_page()})
    p1 = e1.get(U)
    assert p1.ok and not p1.from_cache and p1.render_mode == "static" and p1.network and len(e1.client.page_calls()) == 1
    e2 = make_engine(tmp_path, {U: ok_page()})                 # new process, same cache directory
    p2 = e2.get(U)
    assert p2.ok and p2.from_cache and p2.content_hash == p1.content_hash
    assert e2.client.calls == [] and e2.gate.used == 0 and e2.stats["cache_hits"] == 1     # no robots call, no budget, no network


def test_static_412_falls_back_to_the_browser(tmp_path):
    r = FakeRenderer({U: fixture("paged_list.html")})
    e = make_engine(tmp_path, {U: FakeResponse(412, "no")}, renderer=r)
    p = e.get(U)
    assert p.ok and p.render_mode == "playwright" and "static_blocked_412" in p.notes and len(r.render_calls) == 1
    again = e.get(U)
    assert again.from_cache and again.render_mode == "playwright" and len(r.render_calls) == 1


def test_host_goes_straight_to_the_browser_after_two_refusals(tmp_path):
    urls = [f"https://www.example.edu.cn/p{i}.htm" for i in range(3)]
    r = FakeRenderer()
    e = make_engine(tmp_path, {u: FakeResponse(403, "no") for u in urls}, renderer=r)
    for u in urls:
        assert e.get(u).render_mode == "playwright"
    assert urls[2] not in e.client.calls and len(r.render_calls) == 3


def test_blocked_without_a_browser_is_reported_not_raised(tmp_path):
    e = make_engine(tmp_path, {U: FakeResponse(412, "no")}, renderer=None)
    p = e.get(U)
    assert not p.ok and p.error == "needs_renderer" and p.network


def test_js_shell_goes_to_the_browser_or_is_flagged(tmp_path):
    u = "https://www.cs.sjtu.edu.cn/other.html"
    r = FakeRenderer({u: fixture("sjtu_rendered.html")})
    e = make_engine(tmp_path, {u: FakeResponse(200, fixture("sjtu_shell.html"))}, renderer=r, domains=("sjtu.edu.cn",))
    p = e.get(u)
    assert p.render_mode == "playwright" and "js_shell" in p.notes and "陈海波" in p.markdown
    e2 = make_engine(tmp_path / "b", {u: FakeResponse(200, fixture("sjtu_shell.html"))}, renderer=None, domains=("sjtu.edu.cn",))
    p2 = e2.get(u)
    assert p2.ok and p2.render_mode == "static" and "js_shell_unrendered" in p2.notes


def test_hint_render_always_ignores_a_static_copy_in_the_discovery_cache(tmp_path):
    u = "https://www.cs.sjtu.edu.cn/jiaoshiml.html"
    PageCache(tmp_path / "cache").write(u, u, fixture("sjtu_shell.html"), 200, "static", "utf-8", "old")
    hints = Hints([("www.cs.sjtu.edu.cn", "/jiaoshiml.html", FetchHint("always", ".rc-list .rc-item", 1500))])
    r = FakeRenderer({u: fixture("sjtu_rendered.html")})
    e = make_engine(tmp_path, {}, renderer=r, domains=("sjtu.edu.cn",), hints=hints)
    p = e.get(u)
    assert p.render_mode == "playwright" and r.render_calls == [(u, ".rc-list .rc-item")] and "陈海波" in p.markdown
    assert e.client.page_calls() == []                                  # the plain client was never used for this page


def test_robots_disallow_unreadable_and_browser_read(tmp_path):
    base = "https://www.example.edu.cn"
    e = make_engine(tmp_path, {base + "/robots.txt": FakeResponse(200, "User-agent: *\nDisallow: /private/", {"content-type": "text/plain"}),
                               base + "/private/x.htm": ok_page(), base + "/ok.htm": ok_page()})
    bad = e.get(base + "/private/x.htm")
    assert bad.error == "robots_disallowed" and bad.robots_status == "disallowed" and base + "/private/x.htm" not in e.client.calls
    assert e.get(base + "/ok.htm").ok
    e2 = make_engine(tmp_path / "b", {base + "/robots.txt": FakeResponse(503, "x"), base + "/a.htm": ok_page()})
    assert e2.get(base + "/a.htm").error == "robots_unreadable"
    r = FakeRenderer(robots="User-agent: *\nDisallow: /")                   # the plain client is refused, the browser can read it
    e3 = make_engine(tmp_path / "c", {base + "/robots.txt": FakeResponse(412, "x"), base + "/a.htm": ok_page()}, renderer=r)
    assert e3.get(base + "/a.htm").error == "robots_disallowed" and r.text_calls


def test_off_domain_url_and_off_domain_redirect(tmp_path):
    e = make_engine(tmp_path, {"https://www.example.edu.cn/r.htm": FakeResponse(200, "<html>" + "x " * 400 + "</html>", url="https://evil.com/r.htm")})
    assert e.get("https://evil.com/a").error == "off_domain" and e.client.calls == []
    assert e.get("https://www.example.edu.cn/r.htm").error == "redirect_off_domain"


def test_budget_is_per_university_and_cache_hits_are_free(tmp_path):
    a, b = "https://www.example.edu.cn/a.htm", "https://www.example.edu.cn/b.htm"
    e = make_engine(tmp_path, {a: ok_page(), b: ok_page()}, budget=1)
    assert e.get(a).ok
    assert e.get(b).error == "budget" and e.gate.exhausted
    assert e.get(a).from_cache                                           # free
    e.begin(list(D), 5)
    assert e.get(b).ok


def test_offline_serves_only_the_cache(tmp_path):
    make_engine(tmp_path, {U: ok_page()}).get(U)
    e = make_engine(tmp_path, {}, offline=True)
    assert e.get(U).from_cache
    miss = e.get("https://www.example.edu.cn/new.htm")
    assert miss.error == "offline_miss" and e.client.calls == []


def test_ttl_refetch_and_change_detection(tmp_path):
    now = [datetime(2026, 10, 22, tzinfo=timezone.utc)]
    routes = {U: ok_page("<html><body><p>" + "版本一 " * 80 + "</p></body></html>")}
    e = make_engine(tmp_path, routes, now=lambda: now[0])
    first = e.get(U)
    assert first.ok and not first.changed
    now[0] += timedelta(days=31)
    same = e.get(U)
    assert not same.from_cache and not same.changed                      # fetched again, identical content
    now[0] += timedelta(days=31)
    e.client.routes[U] = ok_page("<html><body><p>" + "版本二 " * 80 + "</p></body></html>")
    assert e.get(U).changed


def test_challenge_page_escalates_and_fails_when_the_browser_is_challenged_too(tmp_path):
    tiny = "<html><body><p>请开启JavaScript 以继续访问</p></body></html>"
    r = FakeRenderer({U: fixture("paged_list.html")})
    e = make_engine(tmp_path, {U: FakeResponse(200, tiny)}, renderer=r)
    p = e.get(U)
    assert p.ok and p.render_mode == "playwright" and "challenge_page" in p.notes
    r2 = FakeRenderer({U: tiny})
    e2 = make_engine(tmp_path / "b", {U: FakeResponse(200, tiny)}, renderer=r2)
    assert e2.get(U).error == "challenge_page"


def test_failure_injection_timeout_http_error_and_breaker(tmp_path):
    urls = [f"https://www.example.edu.cn/f{i}.htm" for i in range(4)]
    routes = {urls[0]: httpx.ConnectTimeout("t"), urls[1]: FakeResponse(503, "x"), urls[2]: httpx.ConnectError("c"), urls[3]: ok_page()}
    e = make_engine(tmp_path, routes)
    assert e.get(urls[0]).error == "timeout"
    assert e.get(urls[1]).error == "http_503"
    assert e.get(urls[2]).error == "connect_error"
    assert e.get(urls[3]).error == "host_blocked"                        # three failures in a row: stop contacting the host
    e2 = make_engine(tmp_path / "b", {urls[0]: FakeResponse(404, "x")})
    assert e2.get(urls[0]).error == "http_404"


def test_gbk_page_through_the_engine(tmp_path):
    html = ("<html><head><meta charset='gb2312'></head><body><ul>" + "<li>清华大学 教授 张三</li>" * 40 + "</ul></body></html>").encode("gbk")
    e = make_engine(tmp_path, {U: FakeResponse(200, html, {"content-type": "text/html"})})
    p = e.get(U)
    assert p.ok and "清华大学 教授 张三" in p.markdown and p.encoding == "gb18030"


def test_renderer_failures_are_reported(tmp_path):
    e = make_engine(tmp_path, {U: FakeResponse(412, "no")}, renderer=FakeRenderer(error="render_unavailable:Error"))
    assert e.get(U).error == "render_unavailable:Error"
    e2 = make_engine(tmp_path / "b", {U: FakeResponse(412, "no")}, renderer=FakeRenderer(status=403))
    assert e2.get(U).error == "render_http_403"


def test_proxy_settings_for_the_browser():
    assert playwright_proxy(None) is None
    assert playwright_proxy("http://127.0.0.1:8080") == {"server": "http://127.0.0.1:8080"}
    assert playwright_proxy("http://user:p%40ss@proxy.example:3128") == {"server": "http://proxy.example:3128", "username": "user", "password": "p@ss"}
