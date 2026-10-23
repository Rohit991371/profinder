import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from profinder_pipeline.extract.css import classify_href, llm_bio_text, parse_list, parse_profile, section_lines, soup_of
from extract_helpers import ECNU, SJTU, TSINGHUA, cfg, html


def test_tsinghua_list_cards():
    r = parse_list(html("tsinghua_list.html"), cfg(TSINGHUA))
    assert len(r.people) == 135 and r.duplicate_urls == 0
    p = r.people[0]
    assert p.name_raw == "冯建华" and p.position_raw == "教授" and p.emails == ["fengjh@tsinghua.edu.cn"]
    assert p.profile_href == "https://www.cs.tsinghua.edu.cn/info/1111/3490.htm"
    assert p.department == "计算机软件研究所" and p.group == "正高"
    assert all(x.emails and x.position_raw for x in r.people)


def test_ecnu_list_cards_and_email_with_spaces():
    r = parse_list(html("ecnu_list.html"), cfg(ECNU))
    assert len(r.people) == 75 and r.duplicate_urls == 1 and r.raw_items == 76
    by = {p.name_raw: p for p in r.people}
    assert by["宋思程"].emails == ["scsong@dase.ecnu.edu.cn"] and by["宋思程"].position_raw == "副教授"
    assert any(p.emails == ["mlan@cs.ecnu.edu.cn"] for p in r.people)          # written as 'mlan @ cs.ecnu.edu.cn'
    assert any("（兼）" in (p.name_raw or "") for p in r.people)


def test_ecnu_href_kinds():
    c = cfg(ECNU)
    r = parse_list(html("ecnu_list.html"), c)
    kinds = [classify_href(p.profile_href, c.list_url, c.personal_url_patterns, ["ecnu.edu.cn"])[0] for p in r.people]
    assert kinds.count("personal") == 71 and kinds.count("none") == 1 and kinds.count("external") == 0 + kinds.count("external")
    assert classify_href("javascript:void(0);", c.list_url, [], ["ecnu.edu.cn"]) == ("none", None)
    assert classify_href("https://bdislab.tech/sxm.html", c.list_url, c.personal_url_patterns, ["ecnu.edu.cn"])[0] == "external"
    assert classify_href("/c7/af/c24531a247727/page.htm", c.list_url, c.personal_url_patterns, ["ecnu.edu.cn"])[0] == "other"


def test_sjtu_rendered_list_people_only_and_menu_links_skipped():
    r = parse_list(html("sjtu_list_rendered.html"), cfg(SJTU))
    assert len(r.people) == 290
    assert all("/jiaoshiml/" in p.profile_href for p in r.people)
    assert all(p.position_raw is None and not p.emails for p in r.people)       # only names + links on the list
    assert r.people[0].department == "并行与分布式系统研究所"
    assert any(p.name_raw.replace("\u3000", "") == "陈榕" for p in r.people)    # U+3000 inside names is cleaned later


def test_tsinghua_profile_interests_and_overrun_guard():
    c = cfg(TSINGHUA)
    a = parse_profile(html("tsinghua_profile_feng_jianhua.html"), c, "https://x/")
    assert a.interests_lines == ["数据库管理系统，数据安全与隐私保护，信息检索"]
    assert a.personal_website == "http://dbgroup.cs.tsinghua.edu.cn/" and a.emails == ["fengjh@tsinghua.edu.cn"] and a.position == "教授"
    b = parse_profile(html("tsinghua_profile_li_guoliang.html"), c, "https://x/")
    assert b.interests_lines == ["大数据挖掘与分析", "人机协作群体计算", "时空大数据处理", "AI和DB的融合"]
    # this page has no heading element between 研究领域 and 讲授课程: teaching text must NOT leak into the interests
    o = parse_profile(html("tsinghua_profile_feng_ling_overrun.html"), c, "https://x/")
    assert o.interests_lines == ["情境感知的数据管理与服务，数据挖掘与仓库，分布式面向对象数据库管理系统"]


def test_sjtu_profile_info_block_and_bio_for_llm():
    c = cfg(SJTU)
    d = parse_profile(html("sjtu_profile_chen_rong.html"), c, "https://x/")
    assert d.name == "陈榕" and d.position == "教授" and d.emails == ["rongchen@sjtu.edu.cn"]
    assert d.department == "并行与分布式系统研究所" and d.personal_website == "https://ipads.se.sjtu.edu.cn/rong_chen"
    assert d.interests_lines is None                                              # no interests section on SJTU pages
    assert llm_bio_text(html("sjtu_profile_chen_rong.html"), c).startswith("陈榕，上海交通大学教授")
    z = parse_profile(html("sjtu_profile_zang_binyu.html"), c, "https://x/")
    assert z.position == "长聘教授" and z.emails == ["byzang@sjtu.edu.cn"]
    assert llm_bio_text(html("sjtu_profile_zang_binyu.html"), c) is None


def test_menu_page_is_not_a_person():
    d = parse_profile(html("sjtu_retired_list_not_a_person.html"), cfg(SJTU), "https://x/")
    assert d.position is None and not d.emails


def test_section_lines_empty_marker_heading():
    s = soup_of("<div class='c'><h4></h4><p>研究领域</p><p>A方向</p><p>B方向</p><h4></h4><p>其他</p><p>x</p></div>")
    assert section_lines(s, ".c", "h4", ["研究领域"]) == ["A方向", "B方向"]
    assert section_lines(s, ".c", "h4", ["研究方向"]) is None
