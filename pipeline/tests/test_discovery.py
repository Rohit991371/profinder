"""Tests for the discovery stage (task 2.5). No network, no database: fake fetcher / client / cursor."""
import json
import tempfile
from pathlib import Path

from profinder_pipeline.discovery import fetcher as fetcher_mod
from profinder_pipeline.discovery.classify import looks_like_admissions, looks_like_faculty_list, looks_like_scholarship
from profinder_pipeline.discovery.crawl import Discoverer, Found, Result, UniInput
from profinder_pipeline.discovery.fetcher import Page, PoliteFetcher
from profinder_pipeline.discovery.links import clean_url, extract_links, visible_text
from profinder_pipeline.discovery.run import _summary_text
from profinder_pipeline.discovery.scoring import score_link
from profinder_pipeline.discovery.search import SearchClient, SearchHit, SearchUnavailable, parse_serper, parse_tavily
from profinder_pipeline.discovery.store import save_result
from profinder_pipeline.registry.textnorm import host_allowed, host_of

DOMAINS = ["uni.edu.cn"]


# ---------- helpers ----------
def html(links=(), text=""):
    return "<html><body>" + "".join(f'<a href="{h}">{t}</a>' for h, t in links) + f"<p>{text}</p></body></html>"


def faculty_html():
    chars = "王李张刘陈杨黄赵周吴徐孙胡朱高林何郭马罗"
    names = [f"{chars[i]}{chars[(i * 7 + 3) % len(chars)]}" for i in range(16)]
    return html([(f"/p/{i}.htm", n) for i, n in enumerate(names)], "教授 教授 教授 副教授 讲师")


ADM = "International students can apply for admission to our master and PhD programmes. " * 6
SCH = "Chinese Government Scholarship for international students. The scholarship covers tuition. " * 2


class FakeFetcher:
    def __init__(self, site):
        self.site, self.domains = site, []
        self.begin([], 0)

    def begin(self, domains, budget):
        self.domains, self.used, self.cache_hits, self.exhausted, self.blocked, self.errors = list(domains), 0, 0, False, [], []

    def get(self, url):
        url = clean_url(url)
        if not host_allowed(host_of(url), self.domains) or url not in self.site:
            return None
        self.used += 1
        return Page(url, url, 200, self.site[url], extract_links(self.site[url], url))


def standard_site():
    return {
        "https://www.uni.edu.cn/": html([("/about", "About"), ("/admissions/", "Admissions"), ("/yxsz/", "院系设置"), ("/sch/", "Scholarships"),
                                        ("https://other.com/n", "News")]),
        "https://www.uni.edu.cn/yxsz/": html([("https://cs.uni.edu.cn/", "计算机科学与技术学院"), ("/math/", "Mathematics")]),
        "https://cs.uni.edu.cn/": html([("/szdw/", "师资队伍"), ("/news/", "新闻")]),
        "https://cs.uni.edu.cn/szdw/": faculty_html(),
        "https://www.uni.edu.cn/admissions/": html(text=ADM),
        "https://www.uni.edu.cn/sch/": html(text="Scholarships coming soon."),
    }


def uni(**kw):
    d = dict(name="Uni", university_id="u1", start_urls=["https://www.uni.edu.cn/"], domains=DOMAINS, seed_urls={})
    d.update(kw)
    return UniInput(**d)


# ---------- links / scoring / classify ----------
def test_extract_links_absolute_chinese_alt_and_skips():
    m = ('<a href="/a.htm#x">师资队伍</a><a href="mailto:a@b.cn">mail</a><a href="javascript:void(0)">js</a><a href="/f.pdf">pdf</a>'
         '<a href="../c/"><img alt="计算机学院"></a><a href="https://x.org/q">ext</a><a href="/a.htm">师资队伍 二</a>')
    ls = {l.url: l.text for l in extract_links(m, "https://www.uni.edu.cn/b/d.htm")}
    assert ls["https://www.uni.edu.cn/a.htm"] == "师资队伍 二"            # fragment stripped, longest text kept
    assert ls["https://www.uni.edu.cn/c/"] == "计算机学院" and "https://x.org/q" in ls
    assert not any(u.endswith(".pdf") or u.startswith("mailto") or u.startswith("javascript") for u in ls)


def test_visible_text_drops_scripts():
    assert visible_text("<script>var a='scholarship';</script><p>Hello <b>you</b></p>") == "Hello you"


def test_scoring_prefers_right_links_and_penalises_recruiting():
    assert score_link("faculty_list", "师资队伍", "https://cs.uni.edu.cn/szdw/") >= 8
    assert score_link("faculty_list", "人才招聘", "https://x/zp") < 0
    assert score_link("department", "计算机科学与技术学院", "https://cs.uni.edu.cn/") >= 5
    assert score_link("department", "Mathematics", "https://math.uni.edu.cn/") < 5
    assert score_link("admissions", "International Students", "https://x/") >= 5
    assert score_link("scholarship", "中国政府奖学金", "https://x/") >= 6
    assert score_link("admissions", "Alumni news", "https://x/") < 4


def test_classifiers():
    f = faculty_html()
    assert looks_like_faculty_list(f, extract_links(f, "https://cs.uni.edu.cn/szdw/"))
    js = html(text="教授 loading...")
    assert not looks_like_faculty_list(js, extract_links(js, "https://x/"))
    assert looks_like_admissions(html(text=ADM)) and not looks_like_admissions(html(text="Welcome to our campus."))
    assert looks_like_scholarship(html(text=SCH)) and not looks_like_scholarship(html(text="Scholarship soon"))


# ---------- PoliteFetcher (fake client) ----------
class R:
    def __init__(self, code, url, body=b"", ctype="text/html; charset=utf-8"):
        self.status_code, self.url, self.content, self.headers = code, url, body, {"content-type": ctype}


class FakeClient:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kw):
        self.calls.append(url)
        v = self.routes.get(url)
        if isinstance(v, Exception):
            raise v
        return v or R(404, url, b"<html>nf</html>")


def _no_sleep():
    fetcher_mod._throttle = lambda host: None


def test_fetcher_allows_blocks_caches_and_budgets():
    _no_sleep()
    cache = Path(tempfile.mkdtemp())
    page = "<html><a href='/x'>hi</a></html>".encode()
    robots_txt = b"User-agent: *\nDisallow: /private/\n"
    c = FakeClient({
        "https://www.uni.edu.cn/robots.txt": R(200, "r", robots_txt, "text/plain"),
        "https://www.uni.edu.cn/ok": R(200, "https://www.uni.edu.cn/ok", page),
        "https://www.uni.edu.cn/private/a": R(200, "https://www.uni.edu.cn/private/a", page),
        "https://www.uni.edu.cn/redir": R(200, "https://evil.com/", page),
        "https://www.uni.edu.cn/pdf": R(200, "https://www.uni.edu.cn/pdf", b"%PDF", "application/pdf"),
        "https://www.uni.edu.cn/boom": ConnectionError("x"),
    })
    f = PoliteFetcher(c, cache)
    f.begin(DOMAINS, 3)
    assert f.get("https://www.uni.edu.cn/ok#frag").links[0].url == "https://www.uni.edu.cn/x"
    assert f.get("https://www.uni.edu.cn/private/a") is None and f.blocked == ["https://www.uni.edu.cn/private/a"]
    assert f.get("https://evil.com/x") is None
    assert f.get("https://www.uni.edu.cn/redir") is None and f.get("https://www.uni.edu.cn/pdf") is None
    assert f.exhausted is False and f.used == 3
    assert f.get("https://www.uni.edu.cn/boom") is None and f.exhausted is True and f.used == 3      # 4th network fetch is refused
    errs = dict((u.rsplit("/", 1)[-1], e) for u, e in f.errors)
    assert errs["x"] == "off_domain" and errs["redir"] == "redirect_off_domain" and errs["pdf"] == "not_html"
    assert c.calls.count("https://www.uni.edu.cn/robots.txt") == 1                # robots read once per host
    f2 = PoliteFetcher(FakeClient({}), cache)                                      # cache hit: no network at all
    f2.begin(DOMAINS, 0)
    assert f2.get("https://www.uni.edu.cn/ok").from_cache and f2.cache_hits == 1


def test_fetcher_robots_unreadable_and_offline():
    _no_sleep()
    f = PoliteFetcher(FakeClient({"https://www.uni.edu.cn/robots.txt": ConnectionError("x")}), Path(tempfile.mkdtemp()))
    f.begin(DOMAINS, 5)
    assert f.get("https://www.uni.edu.cn/a") is None and f.errors[-1][1] == "robots_unreadable"
    g = PoliteFetcher(FakeClient({}), Path(tempfile.mkdtemp()), offline=True)
    g.begin(DOMAINS, 5)
    assert g.get("https://www.uni.edu.cn/a") is None and g.used == 0


# ---------- Discoverer ----------
def test_discovers_all_via_crawl_and_search_and_never_leaves_domain():
    class S:
        def search(self, name, kind, domains):
            assert kind == "scholarship"
            return [SearchHit("https://www.uni.edu.cn/csc/", "CSC")]
    site = standard_site()
    site["https://www.uni.edu.cn/csc/"] = html(text=SCH)
    res = Discoverer(FakeFetcher(site), S()).run(uni(), 30)
    by = {f.kind: f for f in res.found}
    assert by["faculty_list"].url == "https://cs.uni.edu.cn/szdw/" and by["faculty_list"].method == "link_follow" and by["faculty_list"].validated
    assert by["admissions"].url == "https://www.uni.edu.cn/admissions/" and by["admissions"].validated
    assert by["scholarship"].method == "search" and by["scholarship"].url == "https://www.uni.edu.cn/csc/"
    assert res.missing == []
    assert not any("other.com" in f.url for f in res.found)


def test_miss_is_recorded_not_invented_and_search_unavailable_noted():
    class S:
        def search(self, *a):
            raise SearchUnavailable("tavily HTTP 432")
    site = standard_site()
    site["https://www.uni.edu.cn/"] = html([("/about", "About"), ("/admissions/", "Admissions"), ("/yxsz/", "院系设置")])   # no scholarship link
    res = Discoverer(FakeFetcher(site), S()).run(uni(), 30)
    assert res.missing == ["scholarship"] and any(n.startswith("search_unavailable") for n in res.notes)
    res1 = Discoverer(FakeFetcher(standard_site()), S()).run(uni(), 30)            # weak page behind a strong link: kept, flagged unvalidated
    sch = next(f for f in res1.found if f.kind == "scholarship")
    assert sch.validated is False and res1.missing == []
    res2 = Discoverer(FakeFetcher({}), None).run(uni(), 30)                       # nothing reachable at all
    assert res2.missing == ["faculty_list", "admissions", "scholarship"] and "homepage_unreachable" in res2.notes and res2.found == []


def test_seed_is_trusted_even_if_js_and_unvalidated_crawl_candidate_kept():
    site = standard_site()
    site["https://cs.uni.edu.cn/szdw/"] = html(text="教授 loading")               # JS directory: static HTML has no people
    site["https://cs.uni.edu.cn/seedlist"] = html(text="loading")
    r = Discoverer(FakeFetcher(site), None).run(uni(seed_urls={"faculty_list": ["https://cs.uni.edu.cn/seedlist"]}), 30)
    f = next(x for x in r.found if x.kind == "faculty_list")
    assert f.method == "seed" and f.validated is False and "JavaScript" in f.note
    r2 = Discoverer(FakeFetcher(site), None).run(uni(), 30)                         # no seed: crawl keeps a strong candidate, flagged
    f2 = next(x for x in r2.found if x.kind == "faculty_list")
    assert f2.method == "link_follow" and f2.validated is False
    r3 = Discoverer(FakeFetcher(site), None).run(uni(seed_urls={"faculty_list": ["https://cs.uni.edu.cn/missing"]}), 30)
    assert any(n.startswith("seed_unreachable") for n in r3.notes) and any(x.kind == "faculty_list" for x in r3.found)  # falls back to the crawl


def test_homepage_fallback_start_urls():
    site = standard_site()
    r = Discoverer(FakeFetcher(site), None).run(uni(start_urls=["https://down.uni.edu.cn/", "https://www.uni.edu.cn/"]), 30)
    assert r.start_url == "https://www.uni.edu.cn/"


# ---------- search ----------
def test_parsers_and_client_cache_filter_and_limits():
    assert parse_tavily({"results": [{"url": "https://a/x", "title": "T", "content": "c"}, {"title": "no url"}]})[0].url == "https://a/x"
    assert parse_serper({"organic": [{"link": "https://a/y", "title": "T", "snippet": "s"}]})[0].snippet == "s"

    class C:
        def __init__(self, code=200):
            self.n, self.code, self.last = 0, code, None

        def post(self, url, headers=None, json=None, timeout=None):
            self.n += 1
            self.last = (url, headers, json)
            r = type("Rsp", (), {})()
            r.status_code, r.json = self.code, lambda: {"results": [{"url": "https://www.uni.edu.cn/a", "title": "A"}, {"url": "https://evil.com/b", "title": "B"}]}
            return r

    cache = Path(tempfile.mkdtemp())
    c = C()
    sc = SearchClient("tavily", "KEY", c, cache, max_calls=2)
    hits = sc.search("Uni", "admissions", DOMAINS)
    assert [h.url for h in hits] == ["https://www.uni.edu.cn/a"]                   # off-domain hit dropped
    assert c.last[1] == {"Authorization": "Bearer KEY"} and c.last[2]["include_domains"] == DOMAINS
    sc.search("Uni", "admissions", DOMAINS)
    assert c.n == 1 and sc.calls == 1                                                # second call served from cache
    sc.search("Uni", "scholarship", DOMAINS)
    try:
        sc.search("Uni", "faculty_list", DOMAINS)
        raise AssertionError("expected SearchUnavailable")
    except SearchUnavailable:
        pass
    assert "max search calls" in sc.disabled_reason
    bad = SearchClient("tavily", "KEY", C(432), Path(tempfile.mkdtemp()))
    try:
        bad.search("Uni", "admissions", DOMAINS)
        raise AssertionError("expected SearchUnavailable")
    except SearchUnavailable:
        assert bad.disabled_reason


# ---------- store ----------
class FakeCursor:
    def __init__(self, existing=(), pending=None):
        self.sql, self.existing, self.pending, self._last = [], set(existing), pending or {}, None

    def execute(self, q, params=()):
        self.sql.append((" ".join(q.split()), params))
        self._last, self._p = " ".join(q.split()), params

    def fetchone(self):
        return (1,) if self._last and "limit 1" in self._last and (self._p[0], self._p[1]) in self.existing else None

    def fetchall(self):
        return list(self.pending.get((self._p[0], self._p[1]), [])) if self._last and "discovered_by, parser_config" in self._last else []


def test_save_result_writes_targets_only_and_is_careful_with_misses():
    res = Result("Uni", "u1", found=[Found("admissions", "https://www.uni.edu.cn/admissions/", "link_follow", True, 9.0, "https://www.uni.edu.cn/")],
                 missing=["scholarship", "faculty_list"], start_url="https://www.uni.edu.cn/", notes=["x"], searched=["scholarship"])
    cur = FakeCursor(existing={("Uni", "faculty_list")})
    n = save_result(cur, res)
    assert n == {"found": 1, "kept_existing": 0, "miss_recorded": 1, "miss_skipped": 1}
    text = " ".join(q for q, _ in cur.sql)
    assert "ingest.target" in text and "on conflict (url, kind)" in text and "insert into ingest.candidate" not in text
    assert all("ingest.target" in q for q, _ in cur.sql)                            # nothing else is touched
    up = [p for q, p in cur.sql if q.startswith("insert into ingest.target")]
    assert up[0][2:7] == ("admissions", "https://www.uni.edu.cn/admissions/", "link_follow", "allowed", "pending")
    assert up[1][2:7] == ("scholarship", "https://www.uni.edu.cn/", "search", "unknown", "no_page_found")
    assert any(q.startswith("delete from ingest.target") and "no_page_found" in q for q, _ in cur.sql)
    assert json.loads(up[0][7])["validated"] is True


def test_summary_text_and_registry_targets_column():
    r = {"found": [{"kind": "faculty_list", "method": "seed", "validated": None}, {"kind": "admissions", "method": "link_follow", "validated": False}]}
    assert _summary_text(r) == "faculty_list:seed|admissions:link_follow?|scholarship:no_page_found"
    from profinder_pipeline.registry.merge import Merged
    from profinder_pipeline.registry.report import to_row
    assert to_row(Merged(name="Uni"), {"Uni": "x"})["targets_found"] == "x" and to_row(Merged(name="Uni"))["targets_found"] == "not_run"


# ---------- fixes after the first live run ----------
XIDIAN_TITLE = "<title>Guo Jinkun | Admission | Xidian University Homepage</title>"


def test_personal_homepages_are_never_university_pages():
    from profinder_pipeline.discovery.classify import cs_related, is_personal_page, page_title
    assert page_title(XIDIAN_TITLE + "<p>x</p>") == "Guo Jinkun | Admission | Xidian University Homepage"
    assert is_personal_page("https://web.xidian.edu.cn/jkguo/en/zhaosheng.html", "") and is_personal_page("https://a.edu.cn/x", "REN Jian | Profile | Xidian University Homepage")
    assert is_personal_page("https://a.edu.cn/~wang/", "") and not is_personal_page("https://en.sjtu.edu.cn/study", "Study@SJTU")
    page = Page("https://web.xidian.edu.cn/jkguo/en/zhaosheng.html", "https://web.xidian.edu.cn/jkguo/en/zhaosheng.html", 200,
                XIDIAN_TITLE + html(text=ADM), [])
    d = Discoverer(FakeFetcher({}), None)
    assert not d._valid("admissions", page) and not d._valid("scholarship", Page(page.url, page.final_url, 200, XIDIAN_TITLE + html(text=SCH), []))
    good = Page("https://www.uni.edu.cn/admissions/", "https://www.uni.edu.cn/admissions/", 200, "<title>Admissions</title>" + html(text=ADM), [])
    assert d._valid("admissions", good)
    assert cs_related("School of Computer Science faculty") and not cs_related("School of Law faculty")


def test_scholarship_needs_an_international_marker():
    assert looks_like_scholarship(html(text="The scholarship for international students. Scholarship amounts vary."))
    assert not looks_like_scholarship(html(text="Scholarship for local undergraduates. The scholarship is merit based."))


def test_search_hits_are_ranked_before_fetching_and_personal_pages_go_last():
    from profinder_pipeline.discovery.crawl import rank_hit
    best = rank_hit("scholarship", "https://www.uni.edu.cn/en/scholarships", "International Student Scholarships - Uni")
    sub = rank_hit("scholarship", "https://zs.uni.edu.cn/sch", "Scholarships and Financial Aid - Zhishan College, Uni")
    personal = rank_hit("scholarship", "https://web.uni.edu.cn/wang/en/index.html", "Wang | Scholarship | Uni Homepage")
    assert best > sub > personal

    seen = []

    class S:
        def search(self, name, kind, domains):
            return [SearchHit("https://web.uni.edu.cn/wang/en/index.html", "Wang | Scholarship | Uni Homepage"),
                    SearchHit("https://zs.uni.edu.cn/sch", "Scholarships and Financial Aid - Zhishan College"),
                    SearchHit("https://www.uni.edu.cn/en/scholarships", "International Student Scholarships")]

    class F(FakeFetcher):
        def get(self, url):
            seen.append(url)
            return super().get(url)
    site = {"https://www.uni.edu.cn/": html([("/about", "About")]),
            "https://www.uni.edu.cn/en/scholarships": html(text=SCH), "https://zs.uni.edu.cn/sch": html(text=SCH),
            "https://web.uni.edu.cn/wang/en/index.html": "<title>Wang | Scholarship | Uni Homepage</title>" + html(text=SCH)}
    res = Discoverer(F(site), S()).run(uni(domains=DOMAINS + ["web.uni.edu.cn", "zs.uni.edu.cn"]), 30, kinds=("scholarship",))
    assert res.found[0].url == "https://www.uni.edu.cn/en/scholarships" and res.found[0].method == "search"
    assert "https://web.uni.edu.cn/wang/en/index.html" not in seen                  # best hit validated first, personal page never fetched


def test_retry_only_attempts_missing_kinds_and_results_are_merged():
    from profinder_pipeline.discovery.run import merge_retry
    seen = []

    class F(FakeFetcher):
        def get(self, url):
            seen.append(url)
            return super().get(url)
    site = standard_site()
    res = Discoverer(F(site), None).run(uni(), 30, kinds=("admissions",))
    assert [f.kind for f in res.found] == ["admissions"] and res.missing == []
    assert not any("szdw" in u or "/sch/" in u for u in seen)                       # faculty and scholarship were not touched
    prev = {"found": [{"kind": "faculty_list", "url": "https://cs.uni.edu.cn/szdw/", "method": "seed", "validated": True, "score": 0, "via": None, "note": ""}],
            "missing": ["admissions", "scholarship"]}
    new = {"found": [{"kind": "admissions", "url": "https://x/a", "method": "search", "validated": True, "score": 0, "via": None, "note": ""}], "missing": ["scholarship"], "name": "Uni"}
    m = merge_retry(prev, new)
    assert [f["kind"] for f in m["found"]] == ["faculty_list", "admissions"] and m["missing"] == ["scholarship"]


def test_store_keeps_seed_and_validated_targets_and_replaces_stale_pending():
    def res(f):
        return Result("Uni", "u1", found=[f])
    seedrow = {("Uni", "faculty_list"): [("seed", "true")]}
    new = Found("faculty_list", "https://x/other", "link_follow", True, 9.0)
    c1 = FakeCursor(pending=seedrow)
    assert save_result(c1, res(new))["kept_existing"] == 1 and not any(q.startswith("insert") for q, _ in c1.sql)       # seed wins
    weak = Found("admissions", "https://x/weak", "search", False, 0.0)
    c2 = FakeCursor(pending={("Uni", "admissions"): [("link_follow", "true")]})
    assert save_result(c2, res(weak))["kept_existing"] == 1                                                              # validated beats unvalidated
    c3 = FakeCursor(pending={("Uni", "admissions"): [("search", "true")]})
    strong = Found("admissions", "https://x/new", "link_follow", True, 9.0)
    assert save_result(c3, res(strong))["found"] == 1
    assert any(q.startswith("delete from ingest.target") and "status = 'pending'" in q and "url <>" in q for q, _ in c3.sql)   # stale pending removed


def test_explain_lines_show_top_links_and_domain_side():
    from profinder_pipeline.discovery.run import explain_lines
    p = Page("https://www.uni.edu.cn/", "https://www.uni.edu.cn/", 200, html([("https://cs.uni.edu.cn/", "计算机学院"), ("https://cs.other.com/", "Computer Science")]),
             extract_links(html([("https://cs.uni.edu.cn/", "计算机学院"), ("https://cs.other.com/", "Computer Science")]), "https://www.uni.edu.cn/"))
    txt = "\n".join(explain_lines(p, DOMAINS))
    assert "department:" in txt and " on " in txt and "OFF" in txt and "计算机学院" in txt


def test_revalidate_drops_only_validated_non_seed_targets_the_new_rules_reject():
    from profinder_pipeline.discovery.run import revalidate_results
    bad = "<title>Guo | Admission | Xidian University Homepage</title>" + html(text=ADM)
    site = {"https://web.uni.edu.cn/jk/zs.html": bad, "https://www.uni.edu.cn/admissions/": html(text=ADM), "https://www.uni.edu.cn/sch/": html(text="x")}
    fd = lambda k, u, m, v: {"kind": k, "url": u, "method": m, "validated": v, "score": 0, "via": None, "note": ""}
    results = {"Uni": {"found": [fd("admissions", "https://web.uni.edu.cn/jk/zs.html", "search", True),
                                 fd("admissions", "https://www.uni.edu.cn/admissions/", "link_follow", True),
                                 fd("scholarship", "https://www.uni.edu.cn/sch/", "link_follow", False),
                                 fd("faculty_list", "https://cs.uni.edu.cn/seed", "seed", None),
                                 fd("scholarship", "https://www.uni.edu.cn/not-cached", "search", True)],
                       "missing": [], "notes": []}}
    f = FakeFetcher(site)
    out = revalidate_results(results, {"Uni": uni(domains=DOMAINS + ["web.uni.edu.cn"])}, f, Discoverer(f, None))
    assert out == [("Uni", "admissions", "https://web.uni.edu.cn/jk/zs.html", "personal_page")]
    kinds = [(x["kind"], x["method"]) for x in results["Uni"]["found"]]
    assert ("admissions", "link_follow") in kinds and ("scholarship", "link_follow") in kinds and ("faculty_list", "seed") in kinds
    assert ("scholarship", "search") in kinds                                      # not in the cache: cannot be judged, kept


def test_dedupe_plan_keeps_seed_then_validated_then_link_follow():
    from profinder_pipeline.discovery.store import plan_dedupe
    rows = [("U", "faculty_list", "1", "https://a/s", "search", "true"), ("U", "faculty_list", "2", "https://a/seed", "seed", "true"),
            ("U", "admissions", "3", "https://a/x", "search", "true"), ("U", "admissions", "4", "https://a/y", "link_follow", "true"),
            ("U", "scholarship", "5", "https://a/u", "link_follow", "false"), ("U", "scholarship", "6", "https://a/v", "search", "true")]
    keep, drop = plan_dedupe(rows)
    assert {r[2] for r in keep} == {"2", "4", "6"} and {r[2] for r in drop} == {"1", "3", "5"}


def test_faculty_found_via_school_index_when_homepage_has_no_cs_link():
    site = {"https://www.uni.edu.cn/": html([("/jgsz/yxsz.htm", "机构设置"), ("/news/1.htm", "北理工团队在偶极激子的能谷极化方面取得新进展 学院")]),
            "https://www.uni.edu.cn/jgsz/yxsz.htm": html([("https://cs.uni.edu.cn/", "计算机学院"), ("https://math.uni.edu.cn/", "数学学院")]),
            "https://cs.uni.edu.cn/": html([("/szdw/", "师资队伍")]), "https://cs.uni.edu.cn/szdw/": faculty_html()}
    r = Discoverer(FakeFetcher(site), None).run(uni(), 30, kinds=("faculty_list",))
    assert r.found and r.found[0].url == "https://cs.uni.edu.cn/szdw/" and r.found[0].validated


def test_faculty_found_via_university_level_faculty_page_and_school_faculty_page():
    site = {"https://www.uni.edu.cn/": html([("/szdw/lyys.htm", "师资队伍"), ("/zsjy.htm", "招生就业")]),
            "https://www.uni.edu.cn/szdw/lyys.htm": html([("/szdw/xysz.htm", "学院师资"), ("/szdw/gjjq.htm", "国家杰青")]),
            "https://www.uni.edu.cn/szdw/xysz.htm": html([("https://sse.uni.edu.cn/", "计算机科学与工程学院"), ("https://law.uni.edu.cn/", "法学院")]),
            "https://sse.uni.edu.cn/": html([("/faculty/", "师资队伍")]), "https://sse.uni.edu.cn/faculty/": faculty_html()}
    r = Discoverer(FakeFetcher(site), None).run(uni(), 30, kinds=("faculty_list",))
    assert r.found and r.found[0].url == "https://sse.uni.edu.cn/faculty/" and r.found[0].validated
    assert Discoverer(FakeFetcher({"https://www.uni.edu.cn/": html([("/a", "About")])}), None).run(uni(), 30, kinds=("faculty_list",)).missing == ["faculty_list"]


def test_new_school_index_vocabulary_and_reasons():
    assert score_link("dept_index", "机构设置", "https://www.whu.edu.cn/jgsz/yxsz.htm") >= 6
    assert score_link("school_faculty", "学院师资", "https://www.neu.edu.cn/szdw/xysz.htm") >= 8
    d = Discoverer(FakeFetcher({}), None)
    pg = lambda t, u="https://www.uni.edu.cn/x": Page(u, u, 200, t, [])
    assert d._why("scholarship", pg(html(text="Scholarship soon"))) == "too_few_scholarship_words"
    assert d._why("scholarship", pg(html(text="Scholarship for locals. The scholarship is merit based."))) == "no_international_marker"
    assert d._why("admissions", pg(html(text="Welcome"))) == "no_admission_and_international_words"
    assert d._why("faculty_list", pg(html(text="x"))) == "not_a_static_faculty_list" and d._why("admissions", pg(html(text=ADM))) is None


def test_fetcher_stops_contacting_a_host_that_keeps_refusing():
    _no_sleep()
    c = FakeClient({"https://www.uni.edu.cn/robots.txt": R(404, "r", b"<html>nf</html>"),
                    **{f"https://www.uni.edu.cn/p{i}": R(412, f"https://www.uni.edu.cn/p{i}", b"x") for i in range(6)},
                    "https://other.uni.edu.cn/ok": R(200, "https://other.uni.edu.cn/ok", b"<html></html>")})
    f = PoliteFetcher(c, Path(tempfile.mkdtemp()))
    f.begin(DOMAINS, 30)
    for i in range(6):
        assert f.get(f"https://www.uni.edu.cn/p{i}") is None
    assert [u for u in c.calls if "/p" in u and "robots" not in u] == [f"https://www.uni.edu.cn/p{i}" for i in range(3)]   # 4th..6th never sent
    assert [e for _, e in f.errors][:3] == ["http_412"] * 3 and all(e == "host_blocked" for _, e in f.errors[3:])
    assert f.get("https://other.uni.edu.cn/ok") is not None                                                           # other hosts unaffected
    f.begin(DOMAINS, 30)                                                                                              # next university: fresh start
    assert f.get("https://www.uni.edu.cn/p9") is None and f.errors[-1][1] == "http_404"


def test_english_subsite_homepage_also_tries_the_main_site():
    site = {"https://en.uni.edu.cn/": html([("/about", "About")]),
            "https://www.uni.edu.cn/": html([("/jgsz/yxsz.htm", "机构设置")]),
            "https://www.uni.edu.cn/jgsz/yxsz.htm": html([("https://cs.uni.edu.cn/", "计算机学院")]),
            "https://cs.uni.edu.cn/": html([("/szdw/", "师资队伍")]), "https://cs.uni.edu.cn/szdw/": faculty_html()}
    r = Discoverer(FakeFetcher(site), None).run(uni(start_urls=["https://en.uni.edu.cn/", "https://www.uni.edu.cn/", "https://uni.edu.cn/"]), 30, kinds=("faculty_list",))
    assert r.found and r.found[0].url == "https://cs.uni.edu.cn/szdw/"
    seen = []

    class F(FakeFetcher):
        def get(self, url):
            seen.append(url)
            return super().get(url)
    Discoverer(F(standard_site()), None).run(uni(start_urls=["https://www.uni.edu.cn/", "https://uni.edu.cn/"]), 30, kinds=("faculty_list",))
    assert "https://uni.edu.cn/" not in seen                                                                          # a normal www homepage costs no extra fetch
