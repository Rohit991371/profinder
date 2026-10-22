import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from fetch_helpers import FakeRenderer, FakeResponse, fixture, make_engine, ok_page
from profinder_pipeline.fetch.plan import extract_people, find_pagination, person_label, plan_faculty

SJTU = ["sjtu.edu.cn"]


def test_person_label_accepts_names_and_rejects_menu_items():
    for good in ("张伟", "蒋　力", "欧阳修远", "约翰·霍普", "Zhang Wei", "Morgan Peng", "John Edward Hopcroft", "管海兵（兼）"):
        assert person_label(good), good
    for bad in ("首页", "师资队伍", "学院概况", "联系我们", "新闻动态", "Teacher Directory", "College Overview", "Talent Recruitment",
                "Faculty", "A", "Download Zone", "计算机科学与技术系", ""):
        assert person_label(bad) is None, bad


def test_extract_people_from_the_rendered_sjtu_list():
    s = extract_people(fixture("sjtu_rendered.html"), "https://www.cs.sjtu.edu.cn/jiaoshiml.html", SJTU)
    urls = [p.url for p in s.people if p.url]
    assert len(urls) == len(set(urls)) == 11                     # 刘卫文 appears in two institutes: one profile
    assert s.no_link == 2                                        # 孟　魁 and John Edward Hopcroft have no profile page
    assert "https://www.cs.sjtu.edu.cn/jiaoshiml/zangbinyu.html" in urls
    assert not any("lyys" in u or "szdw" in u for u in urls)     # menu entries are not people


def test_empty_static_shell_has_no_people():
    assert extract_people(fixture("sjtu_shell.html"), "https://www.cs.sjtu.edu.cn/jiaoshiml.html", SJTU).people == []


def test_off_domain_person_links_are_counted_not_followed():
    html = "<ul>" + "".join(f"<li><a href='/p/{i}.htm'>张{n}</a></li>" for i, n in enumerate("一二三四五六")) + \
           "<li><a href='https://personal.example.org/~li'>李四</a></li></ul>"
    s = extract_people(html, "https://www.example.edu.cn/l.htm", ["example.edu.cn"])
    assert len([p for p in s.people if p.url]) == 6 and s.off_domain == 1


def test_generic_link_text_uses_the_name_next_to_it():
    html = "<ul>" + "".join(f"<li><h4>{n}</h4><a href='/p/{i}.htm'>更多</a></li>" for i, n in enumerate(["王芳", "李娜", "刘洋", "陈静", "杨磊", "赵敏"])) + "</ul>"
    s = extract_people(html, "https://www.example.edu.cn/l.htm", ["example.edu.cn"])
    assert [p.name for p in s.people] == ["王芳", "李娜", "刘洋", "陈静", "杨磊", "赵敏"]


def test_real_bupt_home_page_has_letter_pagination_and_featured_people():
    html = fixture("bupt_teacher_en_home.html")
    pg = find_pagination(html, "https://teacher-en.bupt.edu.cn/")
    assert len(pg.letters) == 26 and "py=a" in pg.letters[0]
    s = extract_people(html, "https://teacher-en.bupt.edu.cn/", ["bupt.edu.cn"])
    assert len([p for p in s.people if p.url]) >= 10
    assert all("teacher.bupt.edu.cn" in p.url for p in s.people if p.url)


def test_real_chongqing_school_list_is_not_a_faculty_list(tmp_path):
    u = "https://english.cqu.edu.cn/Academics/Faculties_and_Schools.htm"
    e = make_engine(tmp_path, {u: FakeResponse(200, fixture("cqu_faculties_and_schools.html"))}, domains=("cqu.edu.cn",))
    plan = plan_faculty(e, u)
    assert plan.kind == "not_a_faculty_list" and len(plan.profile_urls) < 5


def test_pagination_next_and_numbered_pages():
    base = "https://www.example.edu.cn/szdw/list.htm"
    pg = find_pagination(fixture("paged_list.html"), base)
    assert pg.next_url == "https://www.example.edu.cn/szdw/list2.htm"
    assert pg.numbered == ["https://www.example.edu.cn/szdw/list2.htm", "https://www.example.edu.cn/szdw/list3.htm"]
    last = find_pagination(fixture("paged_list3.html"), "https://www.example.edu.cn/szdw/list3.htm")
    assert last.next_url is None
    js = find_pagination("<a href='javascript:goPage(2)'>下一页</a>", base)
    assert js.js_pagination and js.next_url is None


def _paged_routes():
    b = "https://www.example.edu.cn/szdw/"
    return {b + "list.htm": ok_page(fixture("paged_list.html")), b + "list2.htm": ok_page(fixture("paged_list2.html")),
            b + "list3.htm": ok_page(fixture("paged_list3.html"))}


def test_plan_follows_pagination_once_per_page(tmp_path):
    e = make_engine(tmp_path, _paged_routes())
    plan = plan_faculty(e, "https://www.example.edu.cn/szdw/list.htm")
    assert plan.kind == "faculty_list" and len(plan.list_pages) == 3 and len(plan.profile_urls) == 11
    assert len(set(plan.profile_urls)) == 11 and not any(u.endswith("list2.htm") for u in plan.profile_urls)
    assert len(e.client.page_calls()) == 3                       # each list page fetched once


def test_plan_stops_cleanly_when_the_budget_runs_out(tmp_path):
    e = make_engine(tmp_path, _paged_routes(), budget=2)
    plan = plan_faculty(e, "https://www.example.edu.cn/szdw/list.htm")
    assert "list_truncated_by_budget" in plan.flags and len(plan.profile_urls) == 8 and plan.kind == "list_incomplete"


def test_plan_sjtu_javascript_directory_with_and_without_a_browser(tmp_path):
    u = "https://www.cs.sjtu.edu.cn/jiaoshiml.html"
    shell = {u: FakeResponse(200, fixture("sjtu_shell.html"))}
    r = FakeRenderer({u: fixture("sjtu_rendered.html")})
    e = make_engine(tmp_path, shell, renderer=r, domains=("sjtu.edu.cn",))
    plan = plan_faculty(e, u)
    assert plan.kind == "faculty_list" and len(plan.profile_urls) == 11 and plan.list_pages[0]["render_mode"] == "playwright"
    e2 = make_engine(tmp_path / "b", shell, renderer=None, domains=("sjtu.edu.cn",))
    plan2 = plan_faculty(e2, u)
    assert plan2.kind == "not_a_faculty_list" and "needs_renderer" in plan2.flags


def test_plan_unreachable_list_reports_the_first_error(tmp_path):
    e = make_engine(tmp_path, {"https://www.example.edu.cn/x.htm": FakeResponse(412, "no")}, renderer=None)
    plan = plan_faculty(e, "https://www.example.edu.cn/x.htm")
    assert plan.kind == "list_unreachable" and plan.first_error == "needs_renderer"


def test_plan_follows_the_letter_index_of_a_teacher_portal(tmp_path):
    home = "https://teacher-en.bupt.edu.cn/"
    routes = {home: ok_page(fixture("bupt_teacher_en_home.html"))}
    names = ["Alice Brown", "Bob Carter"]
    for i, url in enumerate(find_pagination(fixture("bupt_teacher_en_home.html"), home).letters):
        routes[url] = ok_page("<ul>" + "".join(f"<li><a href='https://teacher.bupt.edu.cn/p{i}{j}/en/index.htm'>{n}</a></li>" for j, n in enumerate(names)) + "</ul>"
                              + "<p>" + "teacher " * 60 + "</p>")
    e = make_engine(tmp_path, routes, domains=("bupt.edu.cn",), budget=100)
    plan = plan_faculty(e, home)
    assert plan.kind == "faculty_list" and len(plan.list_pages) == 27 and len(plan.profile_urls) >= 52 + 10
    e2 = make_engine(tmp_path / "b", routes, domains=("bupt.edu.cn",), budget=100)
    capped = plan_faculty(e2, home, max_list_pages=5)
    assert "list_truncated_by_max_pages" in capped.flags and len(capped.list_pages) == 5
